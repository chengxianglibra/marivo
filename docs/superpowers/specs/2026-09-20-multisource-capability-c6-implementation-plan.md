# C6：完整时间状态（日历、累计、status-time fold、剩余 validity）实施计划

日期：2026-09-20。状态：计划已落盘，未开始实施。依据 [总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md) §3.3/§5、[C0 清单](2026-09-16-multisource-capability-c0-implementation-plan.md)、[C0 验收](2026-09-16-multisource-capability-c0-acceptance.md)、[C3a 验收](2026-09-18-multisource-capability-c3a-acceptance.md)、[C3b 验收](2026-09-19-multisource-capability-c3b-acceptance.md)、[C4 验收](2026-09-20-multisource-capability-c4-acceptance.md) 与 [C5 验收](2026-09-21-multisource-capability-c5-acceptance.md)。

计划落盘时 HEAD 为 `79a9aaffdbb0fcab9e833ba31a16d6acba2bddce`（分支 `lazy-dataset`，工作区干净），C5 已验收（验收链至 `c32945384`）。本文件先落盘，再实现。DuckDB 是本阶段全部四个子能力的既有参照实现，本阶段不改 DuckDB；其既有旅程作为独立对照 oracle。

## 1. 前置与范围

进入条件为 C3（C3a/C3b 时间契约）与 C4（所需计算状态）已交付——均已验收。交付四个子能力在五个远程后端（PostgreSQL、MySQL、SQLite、Trino、ClickHouse）的解除与真实执行验收：

1. **剩余 validity**：closed-closed interval 与配置 open-end sentinel 在 MySQL/SQLite/Trino/ClickHouse 进入（PostgreSQL 已准入且有 H7 旅程，本阶段只作 oracle 对照，不改 PG 代码）。
2. **语义日历桶**：`ms.calendar_grain(calendar=..., level=...)` 的 day 与非 day 层级桶在五后端准入并真实执行；certified snapshot 经既有 readiness/snapshot store 契约提供，不在分析层新建事实来源。
3. **累计 Metric graph**：`ms.cumulative(...)` 三种 anchor（all-history、`grain_to_date` 周期重置、`trailing` 滚动窗口）在五后端准入并真实执行，含 time_scope 端点裁剪、空窗口语义与 retained 续算。
4. **status-time fold**：半加 measure（`SemiAdditive` 的 status-time 轴 + fold）在五后端按 fold 类别逐单元准入并真实执行；版本化 Entity + status-time fold 的复合形态至少一条真实旅程。

不新增公共 API，不扩展 authoring 参数。DuckDB 不改动。跨源联邦、远程 retained 上传、percentile fold（quantile 归 C7）、distinct-membership/隐藏轴扩展（C7/C8）、`source_requirements`/`requires_source_recompute`（C8 driver）、sampling、exact distinct/quantile/distribution 私有状态（C7）、Event/Lifecycle（C9）、timestamp 坐标版本轴（既有 "qualified native civil-date axes" 拒绝保持）、epoch 解析、表形态扩展保持既有边界。不修改 packaged skills、AGENTS.md、依赖或 Store 格式。

### 子能力 × 后端顺序（本计划锁定的决策）

| 子能力 | 顺序 | 理由 |
| --- | --- | --- |
| 剩余 validity | 四后端同批 | 谓词为纯 ibis 比较/IS NULL 判定，PG 已有旅程即 oracle，风险最低 |
| 日历桶 | 五后端同批 | `bucket()`/`bucket_end()` 已是 CASE-over-date-literals 的纯 ibis 形态，逐后端只验证翻译 |
| 累计 | 五后端同批 | 窗口 join 为既有 coordinate join 形态加一条时间谓词；anchor 解析共享 `cumulative_start` |
| status-time fold | 先 PG（作为远程参照），其余四后端按 live 探测证据逐个开放 | first/last 依赖 argmin/argmax 翻译，各引擎差异真实存在，不能先验承诺全部开放 |

## 2. 已核实的现状（逐符号只读核实，HEAD `79a9aaffd`）

### 2.1 准入 gate 与观测点

全部位于 `marivo/analysis/operators/scalar_support.py::unsupported_reason`：

| Gate | 位置 | 现状 | C6 动作 |
| --- | --- | --- | --- |
| `if definition.temporal_snapshot is not None: return "semantic calendar buckets are not qualified for this backend"` | scalar_support.py:444-445 | 日历桶一律拒绝 | 删除；与 `_admitted_bucket` 的语义 grain 分支同批开放（见下） |
| `_admitted_bucket()` 首行 `if grain.kind != "builtin": return False` | scalar_support.py:139-140 | 语义 grain 先于 :444 在 "this temporal type, parser or bucket is not qualified" 被拒（:435-443 路径） | 扩展为：`grain.kind == "semantic"` 且 `definition.temporal_snapshot.calendar_ref == grain.calendar` 时准入 day 与已发布层级 |
| `if metric.cumulative or metric.source_requirements or metric.requires_source_recompute: return "this Metric requires unqualified cumulative or source-private state"` | scalar_support.py:461-462 | 三形态合一拒绝 | 拆分：仅保留 `source_requirements or requires_source_recompute` 拒绝（诊断文本随之只描述 source-private state）；`metric.cumulative` 放行 |
| `if component.status_time_dimension is not None or component.time_fold is not None: return "status-time folds require additional temporal-state qualification"` | scalar_support.py:468-469 | component 级 fold 一律拒绝 | 换成按 fold 类别的准入判定（新增参数，形态沿 `row_expressions`/`linear_graphs` 先例） |
| node 级 `node.agg not in {...} or node.fold is not None` → "the aggregate requires unqualified private or temporal state" | scalar_support.py:495-500 | `AggregateNodeV1.fold`（fold_override 形态）一律拒绝 | 与 component 级同参数同批准入；两者共用同一 fold 类别资格 |
| `closed_open_null_validity` 参数：closed-closed/sentinel → "validity selection is qualified only for closed_open intervals with NULL open ends" | scalar_support.py:521-526 | MySQL/SQLite/Trino/ClickHouse 传 True（各自 `*_support.py`），PG 未传（全形态准入） | 四后端删除该实参（沿 PG 先例），参数与检查块若无其他消费者一并删除；docstring 同步 |

各 support 文件现状（调用 `scalar_reason` 的实参）：`postgres_support.py`（无 `closed_open_null_validity`）、`mysql_support.py:41`、`sqlite_support.py:27`、`trino_support.py:33`、`clickhouse_support.py:34`（均 True）。DuckDB 无准入 owner（全能力本地引擎）。

### 2.2 既有机制 owner（解除后直接复用，不新建平行实现）

- **语义层**：`ms.cumulative(...)`（`semantic/_authoring_metrics.py:330`，anchor 三形态已完备）、`ms.calendar_grain(...)`（`semantic/_authoring_temporal.py:186`）、`_validate_cumulative_metric`（`semantic/validator.py:1612`，base 必须 tier-1 简单聚合）、snapshot 发布（`semantic/catalog.py:4694 TemporalSnapshotStore.publish`）。
- **编译层**：`compiler/temporal.py::bucket()`（semantic 分支 :23-40：day=cast date；非 day=CASE-over-periods，否则 NULL）、`bucket_end()`（:95-116）、`endpoint_reset_start()`（:119-142）、`cumulative_start()`（:145-167，trailing 含 `boundary_timezone` render/localize）；`compiler/lowering.py::_cumulative_contributions`（:1932-1989，端点×贡献窗口 join）、`_cumulative_bounds`（:1022-1042）、`_distinct_expansion_spine`（:1599，cumulative+distinct 隐藏轴形态——本阶段保持拒绝，见 §7）；status-time fold 源端实现 lowering.py:1885-1925（`__mv_status` 列 + first=argmin/last=argmax/mean/min/max/quantile 分支）。
- **fold 契约与续算**：`observation/fold_contracts.py`（`FoldComponentV1.cumulative`、`MetricFoldAuthorityV1.cumulative`、`FoldAuthorityV1.temporal_snapshot()`）；`operators/rollup.py:437/441/519` 的 `fold_temporal_snapshot` 消费路径（语义 grain 的 retained 重分桶已存在）。
- **运行时交叉校验**：`materialization/temporal_validation.py` 按物理 timestamp 列判定（C3b §5 边界对日历/累计同样成立：native date 轴不进入该检查，编译期守卫拥有 gap/fold 职责）。

### 2.3 既有测试与证据形态（oracle 与翻转点）

- **PG validity 旅程（oracle）**：`tests/test_lazy_postgres_methods.py::test_version_selection_preserves_membership`（:227-312，参数化 `closed_closed`/`sentinel` 正向，成员手算断言）。
- **四后端既有负向（翻转点）**：`tests/test_lazy_mysql_methods.py:471`、`tests/test_lazy_sqlite_methods.py:401`、`tests/test_lazy_clickhouse_methods.py:502`、`tests/test_lazy_trino_methods.py:477`（注意：Trino 参数含 `unresolved_decimal` 变体，属 decimal gate，**不翻转**，只翻转 `closed_closed`/`sentinel` 两个变体）。
- **累计/日历/fold 本地 oracle**：`tests/test_lazy_retained_fold_matrix.py::test_cumulative_runtime_preserves_exact_endpoint_and_partial_selection`（:156）、`test_cumulative_dimension_fold_requires_contiguous_aligned_coverage`（:340）；`tests/test_lazy_temporal_source.py` 与 `tests/test_semantic_cumulative_fiscal_reset.py`、`tests/test_semantic_cumulative_v2_authoring.py`（snapshot 发布 fixture 模式参考）。
- **准入测试模式先例**：`tests/test_lazy_group_b_admission.py:90 test_linear_graph_remains_unqualified_even_after_projection`（分阶段 flag 的负向先例）。
- **fixture 行集（`tests/lazy_execution_fixtures.py::ORDER_VALUES`）**：`(1,cust1,10.0,02-02) (2,cust1,30.0,02-03) (3,cust2,100.0,02-02) (4,cust2,NULL,02-03) (5,cust3,0.0,02-04) (6,cust3,7.0,day NULL)`。

### 2.4 环境

沿用 `tests/multisource_environment/manage.sh` 五服务与既有 opt-in 环境变量（`MARIVO_{POSTGRES,MYSQL,TRINO,CLICKHOUSE}_ANALYSIS_TEST`；SQLite/本地随默认）。数据库组串行；缺服务记为该后端单元未验收（阻塞记录进验收文档），不以模拟替代。Trino 按 C5 先例：启动后须重播 `setup_non_iceberg()`（memory catalog 不耐重启）。本阶段无新 compose/拓扑需求。

### 2.5 技术事实与风险

- **argmin/argmax（first/last fold）翻译差异是本阶段最大不确定点**：DuckDB `arg_min/arg_max`、ClickHouse `argMin/argMax`、Trino `min_by/max_by` 原生；PostgreSQL/MySQL 经 ibis 的降级形态未实测。实施期逐后端 live 探测；探测不支持即该后端该 fold 类别保持结构化拒绝（沿 C4 修正 C/E 的"以实测为准"先例），不得改写源行序语义绕过。
- **percentile fold（`isinstance(fold, tuple)` 分支）归 C7**：五后端全部保持拒绝，负向邻接项验收其结构化拒绝仍在。
- **`identical_to` + `_boolean` 比较 join** 已被 C1 关系旅程在五后端行使，累计窗口 join 不引入新翻译面；新增的时间谓词（`occurrence < end`、`>= lower`）为普通比较。
- **日历桶 SQL 尺寸有界**：CASE 分支数 = snapshot 发布的 period 数（fiscal 日历小而有限）；snapshot 经 `definition.temporal_snapshot` 从已发布 store 读取，无引擎依赖。日历桶旅程使用 native date 轴；timestamp 轴 + 语义 grain 若语义层本就不允许则维持现状（实施期核实，不扩大）。
- **累计空窗口**：`trailing` 空窗口为真零（authoring docstring 契约），不是缺行；与日历"期间外日期 → NULL 桶"是两个不同的空语义，分别断言。
- **版本化 + fold 复合契约**：`observation/metric.py:564-570`——版本化 Metric 缺 status-time fold 直接构造失败；正向旅程须走"semi-additive + 版本化"的合法形态。
- **Trino 未验收遗留**：C3b 记录 Trino 解析轴无执行证据；C6 日历/累计/fold 的 Trino 单元以本阶段真实执行为准，若服务不可用按 §2.4 记阻塞，不沿用"SQL 生成级"冒充通过。

## 3. 实现顺序及拥有者

每个任务独立提交、RED→GREEN 变异验证（回退生产代码新测试转红再恢复）；新增测试前读取 `marivo-test-fixtures` skill，fixture 管理员准备与 reader 执行分离。

1. **剩余 validity 解除（四后端）**。`scalar_support.py` 删除 `closed_open_null_validity` 参数与检查块；四个 `*_support.py` 删实参并同步 docstring。翻转四处负向为正向旅程（closed-closed 与 sentinel 各一，行集与成员断言镜像 PG oracle）；PG 既有旅程不动。变异：恢复检查块 → 四后端新正向转红。
2. **日历桶解除（五后端）**。`_admitted_bucket()` 增加 semantic-grain 分支（calendar_ref 与 snapshot 一致才准入）；`scalar_support.py:444` 快照拒绝删除。旅程：fiscal 日历（两个 fiscal-month 边界）day 层级与非 day 层级桶 + 期间外日期 → NULL 桶行 + 未认证/未发布日历 → 既有 readiness/observe 结构化失败不变。retained：日历 grain 的远程 artifact 本地 `rollup` 重分桶旅程一条。
3. **累计解除（五后端）**。`:461` 拆分条件；`source_requirements`/`requires_source_recompute` 保持拒绝且诊断文本更新。旅程（手算常量见 §4）：all-history、`grain_to_date(month)`（跨月行区分重置与全历史）、`trailing(2 day)`、time_scope 端点部分桶裁剪、trailing 空窗口真零；retained 续算（fold matrix 的累计 oracle 形态移植到远程源）。变异：`cumulative_start` 偏移 → 运行期转红（沿 C3b 变异先例）。
4. **status-time fold 解除（按探测逐后端）**。新增 fold 类别资格参数（形态沿 `linear_graphs` 先例，具体粒度实施期定型并记录）；component 级与 node 级 fold 检查同参数准入。顺序：PG live 探测 argmin/argmax/mean/min/max 五类 fold → 开放证据成立者；其余四后端逐一探测，不支持类别保持结构化拒绝并记录引擎事实。旅程：semi-additive 库存指标 last-by-status 旅程（含 `__mv_status` 非空计数断言）、版本化 + fold 复合旅程一条、percentile fold 五后端拒绝负向。
5. **披露同步**。`docs/specs/analysis/python-analysis-design.md:83-97` 排除清单改写（删 cumulative/status-time/calendar，保留真实剩余项）、`:126-131` "semantic calendar/cumulative extensions are not enabled on remote backends" 句改写；`site/src/content/docs/docs/latest/concepts/analysis-workflow.mdx:199` 与 `zh-cn/docs/latest/concepts/analysis-workflow.mdx:129,153` 双语 parity 改写；实施期 re-grep 双语 latest 其余文件命中即同步。`_disclosure.py:276` effects 当前不含这四项措辞，预计不改——实施期核对漂移测试（`tests/test_lazy_disclosure.py`）是否断言被改事实，命中则同批更新并复跑预算测试。
6. **整体验证与验收文档**。§5 全量命令；验收文档按实际验收日落盘（建议名 `docs/superpowers/specs/YYYY-MM-DD-multisource-capability-c6-acceptance.md`），逐后端单元矩阵 + 阻塞记录 + 回执链接，不覆盖 `multisource-slice-N` 记录。

## 4. Fixture、独立预期值与负向邻接项

复用 `tests/lazy_scalar_source_fixtures.py::registry_for` 与 `tests/lazy_temporal_backend_fixtures.py` 的逐引擎声明源模式；各旅程自建行集（不改共享 `ORDER_VALUES`），管理员建表/reader 旅程分离。

**独立预期值（手算常量，引擎仅作对照；基于 §2.3 行集形态扩展）**：

- 累计 all-history sum（行：02-02→10,100；02-03→30,NULL；02-04→0；03-01→5）：`02-02=110.0`、`02-03=140.0`、`02-04=140.0`、`03-01=145.0`（NULL 不进 sum；day NULL 行不进时间轴）。
- `grain_to_date(month)`：`02-* 三行 = 110/140/140`，`03-01=5.0`（重置可见：全历史在 03-01 为 145.0）。
- `trailing(2 day)`（半开下界 `(end−2d, end]`，边界语义以既有 DuckDB oracle 测试为准，不重立）：`02-02=110.0`、`02-03=140.0`、`02-04=30.0`、`03-01=5.0`。
- fiscal 日历（FM1=2026-02-01..02-15、FM2=02-16..03-15）：FM1=140.0、FM2=5.0；期间外日期（如 03-20）→ NULL 桶行。
- validity closed-closed/sentinel：成员断言镜像 PG `test_version_selection_preserves_membership` 的期望集。
- fold last-by-status：按 status 时间取每键最后库存，再空间 sum——常量在测试内从自建行集手算并硬编码。

**负向邻接项（与正向同批）**：

- percentile fold 五后端 → 结构化拒绝保留。
- `source_requirements`/`requires_source_recompute` 形态 → 拒绝保留且诊断不再误提 cumulative。
- distinct-membership 轴上的累计（隐藏轴扩展形态）→ 拒绝保留（`:421` 既有检查）。
- 未认证日历 / calendar_ref 与 snapshot 不匹配 → 既有结构化失败不变（`bucket()` :24-27 路径）。
- 探测不支持 argmin/argmax 的后端 → first/last fold 结构化拒绝，诊断含引擎事实。
- 既有负向基线不回退：timestamp 版本轴、epoch 解析、sampling、C7/C8/C9 全部单元、跨源联邦的既有拒绝旅程保持通过。

## 5. 验证命令

```bash
# 准入与方法闭包（G1）
make test TESTS='tests/test_lazy_scalar_admission.py tests/test_lazy_group_b_admission.py tests/test_lazy_backend_dispatch.py tests/test_lazy_scalar_execution_adapter.py'
# 时间与 retained 基线（G3/G4）
make test TESTS='tests/test_lazy_temporal_source.py tests/test_lazy_retained_fold_matrix.py'
make runtime-test TESTS='tests/test_lazy_retained_runtime.py tests/test_lazy_temporal_runtime.py tests/test_lazy_temporal_public_runtime.py' RUNTIME_WORKERS=1
# 逐后端 R（服务可用时串行；opt-in 变量以各测试文件头部既有写法为准）
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py' RUNTIME_WORKERS=1
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py' RUNTIME_WORKERS=1
make runtime-test TESTS='tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py' RUNTIME_WORKERS=1
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
# 静态与整门禁
make typecheck TYPECHECK_TARGETS='marivo/analysis marivo/semantic'
make lint-agent LINT_TARGETS='marivo/analysis/operators tests/test_lazy_postgres_methods.py tests/test_lazy_mysql_methods.py tests/test_lazy_sqlite_methods.py tests/test_lazy_clickhouse_methods.py tests/test_lazy_trino_methods.py'
make check-agent
git diff --check
# 披露（site 改动时）
npm --prefix site run verify:content && npm --prefix site run build
```

数据库组串行，先 `bash tests/multisource_environment/manage.sh status <service>` 确认；缺服务或探测不成立导致某单元未验收时记录阻塞，C6 状态保持未完成。不运行 release-check、不启动 MinIO。

## 6. 完成条件

- §1 四项交付在 §3 锁定顺序内逐后端有真实执行回执、独立手算期望值与负向邻接项全批通过；探测不支持的后端×fold 单元在验收矩阵中记为剩余拒绝并附引擎事实。
- §2.1 各 gate 的解除有准入级测试覆盖（接受/拒绝两侧），翻转的既有负向在验收文档逐条列出。
- 披露（analysis spec、双语 site、命中时的漂移断言与 effects）同批更新且预算测试通过。
- `make check-agent` 通过；`git diff --check` 干净。
- 验收文档落盘并链接证据；未验收单元明确列出，不标 C6 完成。

## 7. 排除项

percentile/quantile 与 distribution（C7）；distinct-membership、隐藏轴扩展归因、sampling、Entity correlation/candidate、source driver（C7/C8）；Event/Lifecycle（C9）；跨 datasource 联邦、远程 retained 上传、任意 SQL 入口；timestamp 坐标版本轴与 epoch 解析；表形态扩展；`FINAL`/去重类缓解（ClickHouse 既有铁律沿用）。`source_requirements`/`requires_source_recompute` 与 distinct 展开形态的累计拒绝保留；DuckDB 不改动。packaged skills 只读排查，如需修改另获用户批准。本阶段不启动 C7–C10；C0/总计划与本计划冲突处按总计划 C6 行与本计划 §3 的顺序决策执行，偏差记入验收文档。
