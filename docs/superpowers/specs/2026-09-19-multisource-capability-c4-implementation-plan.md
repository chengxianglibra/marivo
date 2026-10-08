# C4：受限行表达式、Linear graph 与可解析精度 Decimal 实施计划

日期：2026-09-19。状态：计划已落盘，未开始实施。依据 [总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)、[C0 清单](2026-09-16-multisource-capability-c0-implementation-plan.md)、[C1 验收](2026-09-16-multisource-capability-c1-acceptance.md)、[C2 验收](2026-09-16-multisource-capability-c2-acceptance.md)、[C3a 验收](2026-09-18-multisource-capability-c3a-acceptance.md) 与 [C3b 验收](2026-09-19-multisource-capability-c3b-acceptance.md)。

计划落盘时 HEAD 为 `62ccfd252508581ddd59b77b0765d3295f441ab1`，工作区干净，C3b 已提交。本文件先落盘，再实现。

## 1. 前置与范围

C0 对 C4 的界定是"受限行表达式、Linear graph、结果精度可解析 Decimal"，进入条件为 C1 与所需 C2 类型（均已交付）。交付：

1. **计算型 Measure（受限行表达式）**：`@ms.measure(...)` 装饰器体（单返回 ibis 表达式）在六后端声明表源上可被聚合。准入只放行同 Entity 物理列上的行级算术、显式 cast、条件与 NULL 处理；跨行聚合、窗口、跨 Entity 依赖在语义归一化期结构化拒绝。
2. **Linear graph**：`LinearNodeV1`（catalog `ms.linear` 与 runtime `mv.runtime_metric.linear` 两条路径，系数恒为 ±1）在六后端源执行准入；本地 retained rollup 按原方程重算。
3. **可解析精度 Decimal**：每个图节点与行表达式步骤的 decimal(p,s) 由本阶段新增的推导 owner 按锁定规则解析；后端无法保持精确的单元保留拒绝，禁止 float 降级。

不新增公共 API、不扩展 authoring 参数、不开放 tier-2 Metric body、status-time fold、count_distinct、quantile、日历/累计、timestamp validity 扩展、表形态、跨源执行或远程 retained 上传。DuckDB 既有能力不退化；现有 float 结果契约（decimal 输入且结果已为 float，如 ratio/weighted mean → float64）保持不变，不属于本阶段"解除拒绝"的对象。不修改 packaged skills、AGENTS.md、依赖或 Store 格式；不提交、推送、发布；源码证据不代替 C10。

## 2. 已确认的口径与证据（本次只读探测）

### 2.1 当前拒绝点（逐符号核实）

| 拒绝点 | 位置 | 现状 |
| --- | --- | --- |
| "only direct-column measures" | `marivo/analysis/operators/scalar_support.py::unsupported_reason`（sidecar body 无 `source_column` 时返回） | 计算型 Measure 在五后端被拒；DuckDB 无此 gate 但在语义归一化期失败 |
| "the Metric graph contains an unqualified computation" | 同上，`isinstance(node, (AggregateNodeV1, WeightedMeanAggregateNodeV1, SliceNodeV1))` 白名单无 `LinearNodeV1` | Linear 在五后端被拒；DuckDB 源路径已实现（`lowering.py::_values` 有 LinearNodeV1 分支，DuckDB 真实执行通过） |
| "composed Decimal results require resolved precision and scale" | 同上，`metric.logical_type == "decimal"` 且含 Ratio/Linear/WeightedMean/mean 节点 | 全部后端（含 DuckDB）拒绝保持 decimal 的组合结果 |
| "missing declared measure column facts" | `marivo/semantic/metric_graph_lowering.py::_target_measure_type`（`body.source_column is None` 即失败） | 计算型 Measure 连 DuckDB 都无法通过 observe（临时项目端到端复现：`gmv`（直接列）成功返回 `Decimal('20.00')`，`net_total`（计算型）失败于该处） |

### 2.2 ibis decimal 类型推导不可靠（本阶段必须自建推导 owner 的依据）

本地 DuckDB 实测：

| 表达式 | ibis 推导 | 实际引擎结果 |
| --- | --- | --- |
| `AVG(DECIMAL(12,2))` | `decimal(12,2)` | **DOUBLE**（DuckDB 原生 `AVG` 返回 DOUBLE） |
| `DECIMAL(12,2) * DECIMAL(12,2)` | `decimal(12,2)` | **DECIMAL(18,4)**（精度未增长，ibis 推导错误） |
| `DECIMAL(12,2) / DECIMAL(12,2)` | `float64` | DOUBLE |
| `SUM(DECIMAL(12,2))` | `decimal(38,2)` | DECIMAL(38,2)（一致） |

结论：`metric_graph_lowering` 现行 `ibis.table(...).value` 占位推导对 decimal 组合结果不可作为发布类型事实；`_declared_cast`（`lowering.py:364`，要求物理类型为已解析 `dt.Decimal`）与 rollup 会消费错误事实。C4 用本阶段推导 owner 替换全部 decimal 分支的占位推导；非 decimal 类型继续用 ibis 推导（float64/int64 无此类偏差）。

### 2.3 引擎精确算术事实（reader 只读探测，2026-09-19）

| 引擎 | add/sub | mul | div | AVG | 备注 |
| --- | --- | --- | --- | --- | --- |
| DuckDB | 精确（p/s 按标准增长） | 精确 DECIMAL(18,4) 样式 | **DOUBLE，不精确** | **DOUBLE，不精确** | decimal 结果的除法/mean 保持拒绝 |
| PostgreSQL | numeric 精确 | numeric 精确 | numeric（服务器规则，scale 非公开契约） | numeric（scale 非公开契约） | add/sub/mul 结果可精确；div/AVG 的 p/s 不可静态解析 → 保持拒绝 |
| MySQL InnoDB | 精确 | 精确 | 精确（scale = s1 + `div_precision_increment`(默认 4)，precision 随之增长） | 精确（scale = s + 4） | 唯一全链精确候选；`div_precision_increment` 非 4 时拒绝并给修复 |
| SQLite | 无原生 decimal | — | — | — | 不开放 decimal 单元 |
| Trino | 待实施期探测 | 待实施期探测 | 待实施期探测 | 待实施期探测 | 服务停止（C3b 记录）；按实测决定开放单元，实测不通过即保留拒绝并记录 |
| ClickHouse | 精确（≤38） | 精确（内部宽累加） | **Decimal(18,2) 截断 scale，不精确** | **Float64，不精确** | "不将更宽内部累加类型视为更宽公开精度支持"（C0 矩阵）；除法/AVG 保持拒绝 |

### 2.4 已确认的缺陷（随本阶段一并修复，否则激活即静默降级）

- **系数 float 降级**：`LinearTermV1.coefficient: float` 在三处消费点都以 float 参与运算——`lowering.py::_values` 的 `value * term.coefficient`、`metric_graph_lowering.py::visit` Linear 分支的 `typed[...] * term.coefficient`、`operators/rollup.py::_value` 的 `float(value) * coefficient`。对 int64/decimal 项，`×1.0` 可能把结果拖入 float64（或对 Decimal 抛 `TypeError`）。修复：系数恒为 ±1，消费点在 `coefficient.is_integer()` 时按 `int` 字面量相乘，rollup 用保类型乘法。三处同批修改并加负向回归（int64 linear 结果保持 int64；decimal linear 结果保持 Decimal）。
- **mean 冷读方程**：mean 的 primary/hidden 状态为 sum+non_null_count，warm 值由源端 AVG 产生而冷 rollup 由 `sum/count` 重算。MySQL 开放 decimal mean 前必须验证 `AVG(DECIMAL)` 与 `SUM/COUNT` 的 scale 一致（实测 AVG scale = s+4，与 s+4 除法规则一致），并以冷进程等值测试为准。

## 3. Decimal 精度推导契约

新增推导 owner（单一事实来源，semantic 层持有，analysis 与 rollup 消费；拟命名 `marivo/semantic/decimal_precision.py`，实施时如与现有命名冲突再调整并记录）：

| 节点/运算 | 规则（dec(p,s) 记法） | 超界处理 |
| --- | --- | --- |
| sum | `dec(38, s)` | 执行期溢出发布前结构化失败（既有行为） |
| count/count_distinct | `int64`（非 decimal） | — |
| min/max | `dec(p, s)` 原样 | — |
| mean | 仅 MySQL：`dec(min(38, p+4), min(s+4, 30))` 且要求 `div_precision_increment=4`；其余后端保持拒绝 | scale+4 > 30 时拒绝该 Metric（声明 scale 收窄会损失值）；p+4 > 38 不拒绝（均值量级 ≤ 输入上界，可证保值） |
| add/sub（行级与 Linear 项） | `dec(max(p1-s1, p2-s2) + 1 + max(s1,s2), max(s1,s2))` | 推导超 38 → 该单元结构化拒绝（发布前） |
| mul | `dec(min(38, p1+p2), min(38, s1+s2))` | s1+s2 > 38 时截断会导致精度损失 → 拒绝而非静默截断 |
| div（Ratio、行级除法） | 仅 MySQL：`dec(p1+s2+4, s1+4)`；其余后端 decimal 除法保持拒绝 | 超 38/65 → 拒绝 |
| RatioNode（decimal 分量） | 按上条 div 规则；非 decimal 分量维持现有 float 契约 | — |
| WeightedMean（decimal 分量） | 现状已是 float64 结果（不同单元，已合格），不变更 | — |

约束：

1. 行级计算型 Measure 的 decimal 输入走同一规则表推导结果 p/s；推导不可解析（如某后端 div）时该 Measure 在该后端保持准入拒绝，诊断给出具体步骤与原因。
2. 传输校验：接收 Arrow decimal(p', s') 必须与推导声明精确一致（`(p', s') == (p, s)`），不一致结构化失败，不做静默重解释；Parquet 持久化与冷读按 Arrow 原生 decimal 精度。执行边界允许且仅允许一步值精确的类型归一 cast：引擎自然结果类型比声明窄（如 DuckDB 乘法 DECIMAL(18,4) → 声明 dec(24,4)、MySQL 除法实际 (16,6) → 声明 dec(18,6)）或 mean 的 precision 收窄（|AVG| ≤ 输入上界，可证保值）时，可归一到声明 (p,s)；任何 scale 收窄或其余 precision 收窄结构化失败。
3. 除零沿既有契约（RatioNode `zero_division`、`nullif(0)`）；溢出只允许发布前失败。
4. 独立 oracle：测试用 `decimal.Decimal` 常量手算每步期望值；`_declared_cast` 的"未解析即拒绝"语义保持，不放宽。

## 4. 六后端目标矩阵

| 后端 | 行表达式（计算型 Measure） | Linear | Decimal 单元（本阶段新增） | 保持拒绝 |
| --- | --- | --- | --- | --- |
| DuckDB | sum/count/min/max over 计算型 Measure | 是（回归既有路径 + 系数修复） | sum(add/sub/mul 链) 精确 dec；linear dec 精确 | decimal div/mean/AVG；tier-2 Metric body |
| PostgreSQL | 同上 | 是 | decimal add/sub/mul 行级与 sum 精确；linear dec 精确 | decimal div/mean（numeric scale 非公开契约） |
| MySQL | 同上 | 是 | 全链：sum/linear/mean/div 精确 dec（`div_precision_increment=4` 校验） | `div_precision_increment≠4` 时对应单元 |
| SQLite | 同上（float64/int64/string 输入） | 是 | 无 decimal（类型集合不变） | decimal 全部 |
| Trino | 同上（以实测元数据为准） | 是 | 实施期探测后按 §2.3 决定，实测通过才开放 | 未实测通过的全部 decimal 单元 |
| ClickHouse | 同上 | 是 | sum/add/sub/mul/linear 精确 dec（≤38） | decimal div/AVG；不得声称内部宽累加为更宽公开精度 |

计算型 Measure 的允许输入类型＝各后端既有 `supported_type`；int64/float64/string/boolean/timestamp 列上的行表达式全后端开放（string 仅限既有合法用途，不做数值 cast 创造 Measure——现有 validator 契约保持）。Linear 项的 leaf 仍须是已合格的 Aggregate/WeightedMean/Slice/Ratio 组件；Linear 不引入新 leaf 形态。

## 5. 实现顺序及拥有者

1. **语义归一化：计算型 Measure 类型事实**。`metric_graph_lowering.py::_target_measure_type` 对 `body.source_column is None` 的 Measure：以 Entity 声明列构造 `ibis.table` 占位、调用 body callable 得到结果表达式；要求结果是标量 `ir.Value`；op 树 walk 拒绝 Reduction/Window/跨 Table 引用（受限行表达式白名单，语义层唯一 owner，与 `validate_metric_body_ast` 的 AST 白名单互补）；decimal 结果走 §3 owner 推导，非 decimal 用 ibis 推导。`ColumnCollector.body` 已经按 `expression_column_accesses` 收集计算型 body 的物理列依赖（C1 契约复用，不改 collector）。
2. **下推执行**。`analysis/compiler/lowering.py`：`_measure_column` 泛化为按 reference 返回列访问或 `body.callable(table)`（同一次表扫描内完成行表达式计算，不新增源查询）；Linear 分支系数修复（§2.4）；`_values`/`_component` 的 decimal 结果类型事实改由 §3 owner 提供。`scalar_sql_execution` 与各 adapter 不改传输机制；decimal 传输按 §3 第 2 条加精确 p/s 校验。
3. **准入开闸**。`scalar_support.py::unsupported_reason` 新增三个后端资格参数（`row_expressions`、`linear_graphs`、`resolved_decimal_results`，默认 False）：分别放行计算型 Measure、"unqualified computation" 白名单中的 `LinearNodeV1`、以及替换 "composed Decimal" 整体拒绝为 §3 逐单元判定。五个 `*_support.py` 按本文 §4 矩阵逐后端开闸，顺序与对应 runtime 验收一致；任何后端真实执行不满足契约即保持 False 并在验收记录写明。
4. **rollup 方程**。`operators/rollup.py::_value` Linear 分支系数保类型修复；decimal 分量按 §3 规则重算，禁止 `float()` 降级；ratio 分支 float 契约不变。补 linear/decimal 冷进程 rollup 等值测试。
5. **披露**。`docs/specs/analysis/python-analysis-design.md`（§Relational 排除清单移除 linear，补计算型 Measure 与可解析 Decimal 表述）、analysis owner spec 相应段落、live Help 原生注册与预算、动态指导、漂移/可达性/预算测试、`site/` 双语 latest 文档。packaged skills 如需修改须另获用户明确批准。

新增测试前读取 `marivo-test-fixtures` skill。管理员 helper 仅建/清理 UUID 专用表，Dataset 使用既有 reader；本地 DuckDB/SQLite 用 pytest 临时目录。

## 6. Fixture、独立预期值与负向邻接项

拟新增 `tests/lazy_computation_fixtures.py`（扩展 registry：计算型 Measure `net = amount * qty`（amount 为 decimal(12,2)、qty 为 int64）、catalog `ms.linear`（revenue − order_count 语义）、runtime linear 表达式；复用 `lazy_execution_fixtures.make_execution_registry`、`lazy_scalar_source_fixtures.registry_for`、`lazy_scalar_type_fixtures.source_writer/cold_check`、`lazy_retained_fixtures`）、`tests/test_lazy_row_expression_admission.py`（纯静态）、`tests/test_lazy_decimal_precision.py`（纯函数规则）、`tests/test_lazy_computation_runtime.py`（真实执行）。

正向独立预期（全部手算常量，DuckDB 仅作额外对照）：

- 行表达式：orders amount(12,2)×qty → sum = `15.75*3 + 4.25*1 = 51.50`；count/min/max over `amount - 1.00`；条件/NULL 行（`coalesce`、`ifelse`）与 float64 输入混合算术。
- Linear：`revenue − order_count`（混合 unit 冲突在语义层既有拒绝）；int64 项结果保持 int64；decimal linear `dec(12,2)+dec(12,2)` → `dec(14,2)` 值精确；runtime linear 与 catalog linear 同值。
- Decimal：MySQL mean(dec) warm = 手算 `sum/count` 到 s+4 位；冷进程 rollup 等值；MySQL `a/b` 到 s1+4 位手算；DuckDB/CH decimal 除法、PG/CH/DuckDB mean 保持结构化拒绝。
- 冷读：源删除 + 主机 TZ 改变后新进程读取与 rollup 零源查询；parts 中 decimal 列 p/s 与 Arrow 一致。

负向邻接项（与正向同批验收）：

- 计算型 Measure body 内 `sum(...)`/窗口/跨 Entity 引用 → 语义加载期结构化拒绝，不得进入执行。
- 引用未声明列的计算型 Measure → 沿 C1 "unknown necessary column" 拒绝。
- decimal 除法/mean 在非 MySQL 后端、`7hour` 式"看起来能算"的单元 → 保持拒绝且诊断说明引擎事实原因。
- Linear 系数必须是 ±1：伪造非 ±1 系数的 graph 载荷构造拒绝（`LinearTermV1` 无公共构造入口，持久化层校验）。
- 传输 p/s 与推导不一致 → 结构化失败，不静默 cast。
- 已声明未使用列、既有 float ratio/weighted mean、日历/累计/count_distinct/quantile/Event/Lifecycle 行为不因本阶段改变。

## 7. 验证命令

```bash
# 静态与纯函数（G4 + 新增）
make test TESTS='tests/test_lazy_row_expression_admission.py tests/test_lazy_decimal_precision.py tests/test_lazy_retained_fold_matrix.py tests/test_lazy_group_b_admission.py tests/test_lazy_scalar_admission.py tests/test_lazy_postgres_admission.py tests/test_lazy_backend_dispatch.py tests/test_lazy_scalar_execution_adapter.py tests/test_lazy_duckdb_execution_adapter.py'
# 本地 DuckDB/SQLite 真实执行
make runtime-test TESTS='tests/test_lazy_computation_runtime.py tests/test_lazy_runtime_metric.py tests/test_lazy_retained_runtime.py tests/test_analysis_decimal_e2e.py tests/test_analysis_cumulative_decimal.py' RUNTIME_WORKERS=1
# 逐后端（服务可用时串行执行；不默认启动/停止服务）
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_computation_runtime.py tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py' RUNTIME_WORKERS=1
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_computation_runtime.py tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py' RUNTIME_WORKERS=1
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_computation_runtime.py tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_computation_runtime.py tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
make typecheck TYPECHECK_TARGETS='marivo/semantic marivo/analysis'
make lint-agent LINT_TARGETS='marivo/semantic marivo/analysis tests/lazy_computation_fixtures.py tests/test_lazy_row_expression_admission.py tests/test_lazy_decimal_precision.py tests/test_lazy_computation_runtime.py'
make check-agent
npm --prefix site run verify:content
npm --prefix site run build
git diff --check
```

数据库组串行；先检查现有服务（`bash tests/multisource_environment/manage.sh status ...`），缺服务记为未验收，不运行 release-check 或启动 MinIO。Trino decimal 探测在实施期执行并写入验收记录。

## 8. 完成条件

§4 矩阵中每个"开放"单元有真实提交 SQL 与独立期望值；负向邻接项同批通过；§2.4 两个缺陷修复有回归；披露（spec、Help、双语 site）同步；`make check-agent` 通过。任何未达成单元写入验收记录的剩余拒绝项，C4 状态保持未完成。

验收文档：`docs/superpowers/specs/2026-09-19-multisource-capability-c4-acceptance.md`（日期用实际验收日），机器可读回执沿用 `-execution-receipts.json` 前缀并由验收文档链接，不覆盖 `multisource-slice-N` 记录。

## 9. 排除项

不实施：tier-2 Metric body、status-time fold、count_distinct、quantile/distribution、sampling、Entity 方法、Event/Lifecycle、日历/累计、timestamp validity 扩展、多单位桶变更、表形态（C5）、跨 datasource 联邦、远程 retained 上传、任意 SQL 入口、近似/截断替代。第一轮的 C5–C10 不因本阶段启动；C4 完成不标记 Linear 之外的高阶组合（如 Linear 嵌套 ratio 后的 decimal 全链）自动可用，逐单元以 §3 规则与验收为准。
