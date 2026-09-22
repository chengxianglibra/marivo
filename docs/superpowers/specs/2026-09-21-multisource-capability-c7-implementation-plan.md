# C7：精确集合与分布（exact distinct，随后 quantile/distribution）实施计划

日期：2026-09-21。状态：已实施并验收；2026-09-22 的执行证据见 [C7 验收记录](2026-09-22-multisource-capability-c7-acceptance.md)。依据 [总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md) §2/§3.6/§5、[C0 清单](2026-09-16-multisource-capability-c0-implementation-plan.md) C7 行、[C0 验收](2026-09-16-multisource-capability-c0-acceptance.md) §2.2、[C6 验收](2026-09-21-multisource-capability-c6-acceptance.md)。

计划落盘时 HEAD 为 `7fcdaf047`（分支 `lazy-dataset`，工作区两处与本阶段无关的未跟踪文件：`docs/specs/analysis/README.md`、`docs/specs/analysis/evidence-engine/`，保持不动），C6 已验收（验收链末位 `8946cd711`，已合并为 `7fcdaf047`）。本文件先落盘，再实现。DuckDB 是 exact distinct 与 quantile/distribution 私有状态的既有参照实现，本阶段不改 DuckDB；其既有旅程作为独立对照 oracle。

## 1. 前置与范围

进入条件为所需类型（C1–C4 已交付：各后端标量类型、时间轴、计算 measure）与私有状态设计（本文 §2 锁定）已明确。交付两个子能力在五个远程后端（PostgreSQL、MySQL、SQLite、Trino、ClickHouse）的解除与真实执行验收：

1. **exact distinct membership**：`count_distinct` Metric（直接列 measure 键与 Entity 身份键）在远程后端按既有 source-private 契约进入——坐标×key 成员关系、结构化完整性校验（非空、pair 唯一、坐标支撑、端点复现）、私有 Parquet part 写出、冷读续算（rollup/compare/attribution），全程不传输原始 key 到本地。
2. **quantile/distribution**：`median`/`percentile` Metric（`linear_interpolation@v1`）在远程后端按既有 source-private 契约进入——坐标×value×frequency 分布关系、端点插值复现校验、私有 Parquet part 写出与续算；`duckdb_tdigest@v1` 方法保持 DuckDB 独占。

不新增公共 API，不扩展 authoring 参数，不改方法定义（`count_distinct`、`median`/`percentile`、`linear_interpolation@v1` 语义原样）。percentile-tuple status-time fold（`component.time_fold` / `node.fold` 为 `(percentile, q)` 形）保持拒绝——它是 fold lowering 的量化分支，不是本阶段的 Metric 级 quantile；C6 已将其拒绝钉为负向邻接项，本阶段不翻转。跨源联邦、远程 retained 上传、`sampling`、Entity correlation/candidate/driver（C8）、Event/Lifecycle（C9）、`duckdb_tdigest@v1` 远程化、`DistributionAuthorityV1`/`DistinctMembershipAuthorityV1` 契约变更保持既有边界。不修改 packaged skills、AGENTS.md、依赖或 Store 格式。

### 子能力 × 后端顺序（本计划锁定的决策）

| 子能力 | 顺序 | 理由 |
| --- | --- | --- |
| exact distinct | 先 SQLite（本地文件、快速 RED→GREEN 变异轮），再 PostgreSQL，其余三后端按 live 探测证据逐个开放 | SQLite 无服务依赖可作远程形态参照；PG 是 C6 fold 的远程参照先例；成员关系谓词是纯 ibis（semi-join/distinct/group-by），各引擎翻译风险低，但 identity struct 列与文本 collation 键（MySQL/SQLite）有真实引擎差异，必须逐个实测 |
| quantile/distribution | SQLite 之后同构推进：SQLite → PostgreSQL → 其余三后端逐个 | 分布关系与端点插值同为纯 ibis 形态（group-by + 窗口累计和），主要风险是各引擎 float 累计顺序导致的 `_close` 容差边界，逐后端 live 探测插值精确性后再开放 |

first-backend 不预设全部开放：探测不成立的引擎×形态保持结构化拒绝并在验收文档记录引擎事实（沿 C6 MySQL first/last fold 先例）。

## 2. 私有状态设计（本阶段锁定的决策）

- **membership 状态形态**：每贡献组一个 `(keys…, __mv_distinct_key)` 去重关系（`lowering.py:1840-1844` 既有 lowering 形态），列与类型由 `retained.py::membership_schema` 验证。远程执行沿用同一编译产物，不允许后端私有改形（如 `COUNT(DISTINCT)` 标量替代、`GROUP BY` 位图、HLL/Sketch）。
- **distribution 状态形态**：每贡献组一个 `(keys…, __mv_distribution_value, __mv_distribution_frequency)` 频率关系（`lowering.py:1866-1873` 既有形态），端点由 `distribution.py::frequency_quantile` 精确插值（窗口累计频率法），禁止引擎原生 `quantile*`/`percentile*` 聚合替代插值复现。
- **重叠组 rollup**：`Entity` 键 membership 的 `spatial_merge=("blocked","sum")` 与 measure 键的 `("blocked",)` 既有契约不扩大；本地续算经既有 `fold_memberships`/`private_part_validations` 路径重放端点方程（`test_lazy_distinct_fold.py` 的 oracle 形态），不引入第二套合并规则。distribution 的 `time_merge="blocked"`/`spatial_merge="blocked"` 保持——不同组不合并分布。
- **数值算法**：插值期望值用测试内手算常量（非引擎互抄）；`_close` 容差（`1e-12`/`1e-9` 相对）只在端点复现校验处使用，float 累计顺序差异不构成放宽定义的理由——插值算式本身在计划 §4 中逐步列出。
- **传输权限**：私有关系只允许两类过线产物——`(a)` 独立 Parquet part 的流式批（`independent_parts`，经 `guard_part_transfer` 布局守护），`(b)` 标量违规计数（`CompiledValidation`/`membership_integrity_sql`/`validate_distribution_relation`）。原始 key/值行进入本地结果、Evidence、metadata 或 statement inventory 均为失败（复用 `assert_no_raw_keys`/`guard_membership_transport`/`guard_distribution_transport` 守护）。
- **禁止事项**：禁止丢弃续算状态（只回 `COUNT DISTINCT` 标量）；禁止近似替代精确定义（`approx_quantile`、`tdigest`、`HLL++`）；禁止把私有 part 转入 generic retained 迁移（`reject_source_private_transfer` 既有拒绝保持）；禁止改写 `QuantileMethodV1` 词表或放宽 `supported_distinct_key_type`。

## 3. 已核实的现状（逐符号只读核实，HEAD `7fcdaf047`）

### 3.1 准入 gate 与翻转点

全部位于 `marivo/analysis/operators/scalar_support.py::unsupported_reason`：

| Gate | 位置 | 现状 | C7 动作 |
| --- | --- | --- | --- |
| `if definition.distinct_memberships or definition.distributions: return "membership and distribution state require a source-private implementation"` | scalar_support.py:461-462 | 两形态合一只允许 DuckDB | 换成按形态×后端的准入判定（新增参数，形态沿 `status_folds` 先例）；诊断文本区分 membership 与 distribution |
| node 级 `if node.agg not in {"sum","count","min","max","mean"}` | scalar_support.py:573 | `count_distinct`/`median`/`percentile` 一律拒绝 | 同参数准入（`count_distinct` 与 quantile 各自的资格集） |
| `if isinstance(node.fold, tuple): return …` 与 component 级 percentile-tuple 检查（:517-519 注释与 :575-577 一致） | scalar_support.py:517-519, 575-577 | percentile **fold** 一律拒绝 | **保持拒绝**（非本阶段交付；`_FOLD_REQUIREMENTS` 中 `metric.source_quantile@v1` 的豁免语义不变——它只豁免 requirement 集合检查，fold 形态仍在 :517/:575 被拒） |
| registry `implementation()` 中 `source_private_state` 短路 `local_method=None`（registry.py:268-277） | operators/registry.py | 私有状态形态在远程后端无 local_method | 放开该短路（远程 `SourceStep` 本就经共享 Observation lowerer 求值，`local_method` 只约束本地续算所有权）——实施期核实短路边界，确保远程 primary 执行不受影响、本地二段（rollup/attribute）仍拒非 DuckDB |

各 `*_support.py` 的 `scalar_reason(...)` 调用点即逐后端翻转开关：`postgres_support.py:39-50`、`mysql_support.py:41-54`、`sqlite_support.py:23-33`、`trino_support.py:33-42`、`clickhouse_support.py:34-43`。

### 3.2 既有机制 owner（解除后直接复用，不新建平行实现）

- **语义层**：`make_distinct_membership`（`observation/distinct_contracts.py:66`，measure 键 `source_column` 直列 + Entity 键 `identity_signature` struct）、`supported_distinct_key_type`（:46，bool/int/float/decimal/string/binary/date/time/timestamp/uuid）、`make_distribution`（`observation/distribution_contracts.py:27`，要求 `linear_interpolation@v1` 或显式 method、numeric 直列 measure、非 cumulative、单 component）、`QuantileMethodV1`（`semantic/_quantile.py`）。
- **编译层**：`lowering.py::_component`（count_distinct 私有关系 :1840-1844、quantile 私有关系 :1866-1873、端点 state :1846-1848 `value.nunique()` / :1874-1881 `source_quantile`）、`compiler/distinct.py::membership_validations`（:118，四类结构化断言）、`compiler/distribution.py::distribution_validations`（:171，五类断言含端点插值复现）、`private_parts.py`（统一 dispatch）、`_distinct_expansion_spine`（:1603，隐藏轴展开形态——C7 保持其既有准入边界不动）。
- **运行时**：`admission.py` recipe 分支（:1303-1330 `validate_source_private_relation` + `independent_parts` 流式写出）；`retained.py::validate_source_private_relation`（:215，按 role 分派 membership_integrity_sql / validate_distribution_relation）；`materialization/distribution.py::validate_distribution_relation`（:20，标量违规-only）；`duckdb_statements.py::membership_integrity_sql`（:122——**注意**：该 SQL 以 duckdb 方言渲染 struct_extract/IS NOT DISTINCT FROM，远程后端需方言无关等价实现，见 Task 3 设计）。
- **rollup 续算**：`compiler/distinct_fold.py::fold_memberships`、`operators/rollup.py` retained 路径、`test_lazy_distinct_fold.py` oracle 形态。

### 3.3 既有测试与证据形态（oracle 与翻转点）

- **distinct oracle**：`tests/test_lazy_distinct_fixtures.py`（`DISTINCT_ORDER_VALUES` 行集含跨组重叠键、双侧键、NULL 键；`guard_membership_transport` 断言 key 不进 Arrow payload）、`test_lazy_distinct_compiler.py`、`test_lazy_distinct_fold.py::test_time_fold_uses_final_original_evaluation_membership`（rollup 续算方程）、`test_lazy_distinct_numeric.py`、`test_lazy_distinct_publication.py`、`test_lazy_distinct_source_runtime.py`、`test_lazy_distinct_runtime_acceptance.py::assert_no_raw_keys`。
- **distribution oracle**：`tests/lazy_distribution_fixtures.py`（`VALUES` 行集含重复值→frequency>1；`guard_distribution_transport`）、`test_lazy_distribution_compiler.py`、`test_lazy_distribution_numeric.py`、`test_lazy_distribution_failures.py`、`test_lazy_distribution_topk.py`、`test_public_quantile_input.py`。
- **负向钉（本阶段部分翻转）**：`tests/test_lazy_backend_dispatch.py::test_production_backend_rejects_unqualified_shape`（参数化含 `median`，五后端 place 拒绝——**翻转**：median 在已开放后端应 place 成功，该测试改为按资格集分叉断言）；`tests/test_lazy_postgres_admission.py::test_unsupported_median_reports_postgres_placement_without_source_io`（:181，PG median 拒绝——**翻转**为正准入或改语义保留负向，实施期按 PG 探测结果决定并在验收文档记录）。
- **保持拒绝的负向**：`tests/test_lazy_status_fold_admission.py::test_percentile_fold_is_rejected_while_scalar_folds_may_open`（:179，percentile **fold** 拒绝不翻转）；`_disclosure.py` effects 不含 distinct/quantile 措辞（已 grep 核实）。

### 3.4 环境

沿用 `tests/multisource_environment/manage.sh` 五服务与既有 opt-in 环境变量（`MARIVO_{POSTGRES,MYSQL,TRINO,CLICKHOUSE}_ANALYSIS_TEST`；SQLite/本地随默认）。数据库组串行；trino/clickhouse 互斥启停（C6 先例）；Trino 启动后重播 `trino_analysis.setup()`（本阶段走 iceberg.analysis 路径）。缺服务记该后端单元未验收（阻塞进验收文档），不以模拟替代。

### 3.5 技术事实与风险

- **`membership_integrity_sql` 方言边界是本阶段最大不确定点**：现有实现用 duckdb 方言（`struct_extract`、`IS NOT DISTINCT FROM`、`string_agg`）。远程实现改为 ibis 表达式形态（`membership_validations` 已是纯 ibis 的同语义断言集）或逐后端方言渲染，实施期以 live 探测定型；`IS NOT DISTINCT FROM` 在 MySQL 无原生形式，需 NULL 安全等价展开（`<=>` 不可用于 ibis 参数化，采用 `(a=b OR (a IS NULL AND b IS NULL)) AND NOT (a IS NULL AND b IS NULL)` 类展开或改走 `membership_validations` ibis 路径——倾向后者，零 SQL 文本）。
- **Entity 键 struct 列远程传输**：PG/SQLite/Trino/ClickHouse 的 struct/distinct 语义各异；identity struct 作 grouping key 在部分引擎（SQLite 无 struct）不可用——Entity 键 membership 的远程单元按后端逐个探测，不成立的记录引擎事实保持拒绝，不允许降级为字符串拼接身份（会改变相等语义）。
- **quantile 端点插值的窗口翻译**：`frequency_quantile` 用 cumulative window + 双标记查询；SQLite/MySQL 窗口函数可用但 `float` 累计顺序敏感，`_close` 容差吸收量级需 live 探测确认（fixture 值域小，绝对误差应远小于 1e-9）。
- **非有限值拒绝**：`distribution_validations` 的 `_finite` 断言已在编译产物中；ClickHouse Float64 `inf/nan` 传输路径已在 C2 验证拒绝，本阶段不新增传输面。
- **语句角色预算**：新增语句角色（`engine_check.membership_schema`、`engine_check.membership_integrity`、`engine_check.<role>.{values,unique,support,endpoint}`、`part.<role>`）已有本地先例；远程首开后 `test_lazy_statement_statistics.py` 若断言被改事实需同批更新（实施期核对）。

## 4. Fixture、独立预期值与负向邻接项

复用 `tests/lazy_distinct_fixtures.py` 与 `tests/lazy_distribution_fixtures.py`（不改共享行集）；远程旅程经 `lazy_temporal_backend_fixtures.py` 的 `_declared_source` 逐引擎模式或各 `test_lazy_<backend>_methods.py` 既有建表 helper 建表，管理员建表与 reader 旅程分离。

**独立预期值（手算常量，引擎仅作对照）**：

- distinct（`DISTINCT_ORDER_VALUES` + customers region EU/US/NULL）：whole-scope `count_distinct(tenant)` = `{shared,old,new,extra}` = **4**（两个 NULL 不计）；`by region`：EU（customer 1,2 的订单）= `{shared}` ∪ … 手算在测试内从行集硬编码——**实施时在旅程测试文件头注明手算式**（沿 C6 `*_methods.py` 参数化表先例）；两个时间 scope 的重叠键 `shared` 跨期出现，续算/rollup 断言最终评估成员集而非期数和。
- quantile（`VALUES` 行集）：web 2026-01 = `[1.0,1.0]` 中位数 **1.0**；store 2026-01 = `[5.0,9.0]` 中位数 **7.0**（线性插值）；web 2026-02 = `[2.0,8.0]` = **5.0**；store 2026-02 = `[4.0,4.0]` = **4.0**（frequency=2 的单值，不重复计数）；q=0.25/0.9 变体手算插值常量在测试内列出。
- 端点复现断言（`*.endpoint`）：primary 端点列值 == 分布关系插值重放值，逐组硬编码。

**负向邻接项（与正向同批）**：

- percentile **fold** 五后端 → 结构化拒绝保持（`test_lazy_status_fold_admission.py` 既有钉）。
- `duckdb_tdigest@v1` 方法 → 远程拒绝（method 词表边界；诊断明确"仅 DuckDB qualified"）。
- computed measure 键 / computed measure 值（`source_column is None`）→ 拒绝保持（`make_distinct_membership`/`make_distribution` 前置返回 None，形态回退到 source-private rejection）。
- 未支持 key 类型（如 Array/JSON 声明）→ `supported_distinct_key_type` 既有拒绝保持。
- cumulative/`_distinct_expansion_spine` 隐藏轴形态与 source requirement 组合 → 既有拒绝边界不回退（`:421` distinct-axis cumulative 检查、requirement 豁免集不含 `metric.source_distinct@v1`）。
- 私有 part generic 迁移 → `reject_source_private_transfer` 既有拒绝保持。
- 既有负向基线不回退：sampling、C8/C9 全部单元、跨源联邦、远程 retained 上传保持通过（`make check-agent` 全绿覆盖）。

## 5. 实现顺序及拥有者

每个任务独立提交、RED→GREEN 变异验证（回退生产代码新测试转红再恢复）；新增测试前读取 `marivo-test-fixtures` skill，fixture 管理员准备与 reader 旅程分离。

1. **准入参数与 registry 短路解除（静态，无 I/O）**。`scalar_support.py` 新增 `distinct_memberships: frozenset[Literal["measure","entity"]] = frozenset()` 与 `distributions: frozenset[Literal["linear_interpolation"]] = frozenset()` 两参数（形态沿 `status_folds` 先例）；:461-462 改为按 `membership.target_kind`/`authority.quantile.method` 判定，诊断文本分列两种形态；:573 node 级 `agg` 白名单同参数准入（`count_distinct` 入 distinct 集、`median`/`percentile` 入 distribution 集）；registry `implementation()` 的 `source_private_state` 短路按"后端注册存在才短路 local_method"改写。五后端 `*_support.py` 暂传空集（行为零变化），测试钉：空集时既有拒绝逐字保留（负向不回退的守护轮）。RED→GREEN：参数传入前新准入测试红、参数接线后绿。
2. **SQLite distinct 旅程（首后端）**。`test_lazy_distinct_runtime.py` 形态移植到 SQLite 声明源：`sqlite_support.py` 传 `distinct_memberships=frozenset({"measure"})`（Entity 键按 §3.5 探测结果决定是否同批）；旅程：observe `distinct_buyers`/`distinct_orders` + region/channel 维度 + 聚合 → 私有 part 写出 → 主结果手算对齐（region×channel 成员计数）→ 冷读 rollup（`test_lazy_distinct_fold` oracle 形态）→ `assert_no_raw_keys` 语句/Evidence 守护。`validate_source_private_relation` 的 integrity 路径在 SQLite 上 live 探测定型（ibis 断言路径优先）。变异：`membership_integrity_sql` 语义弱化（如去掉 pair_unique 检查）→ 旅程转红。
3. **quantile/distribution 静态与 SQLite 旅程**。`sqlite_support.py` 传 `distributions=frozenset({"linear_interpolation"})`；旅程：median/percentile(q=0.25/0.9) 按维度聚合 → 端点手算对齐 → 私有 distribution part 写出 → `guard_distribution_transport` 守护 → 冷读续算（retained 端点重放）。`frequency_quantile` 的窗口形态在 SQLite live 探测插值精确性；不成立则该单元保持拒绝并记录。负向：percentile fold、tdigest 远程拒绝同批钉。
4. **PostgreSQL distinct + distribution**。沿 Task 2/3 形态在 `test_lazy_postgres_methods.py` 增旅程；`postgres_support.py` 接参数；PG 上 struct Entity 键可探测成立则同批开放 Entity 键 membership（手算 struct 语义断言），不成立记录引擎事实。变异沿 Task 2。
5. **其余三后端逐个探测开放（MySQL → Trino → ClickHouse）**。每后端先 live 探测（distinct 键相等/collation/struct、quantile 窗口插值、integrity SQL 翻译），再决定开放单元集；`*_support.py` 传参 + methods 旅程 + 负向钉。MySQL 文本键按既有 `utf8mb4_0900_bin` collation 契约；ClickHouse Nullable 键按 C2 传输契约。探测不成立单元在验收文档记录引擎事实（沿 C6 MySQL first/last 先例）。
6. **披露同步**。`docs/specs/analysis/python-analysis-design.md`：排除清单句（:93-97 "exact distinct membership, quantile/distribution state"）改写为按后端矩阵陈述已开放单元与剩余拒绝；status-time fold 段落 percentile 句核对不动；§3.4 累计排期句不动。`site/src/content/docs/docs/latest/concepts/analysis-workflow.mdx:211-213` 与 `zh-cn/.../analysis-workflow.mdx:135-137` 双语 parity 改写（sampling/Entity correlation/candidate/driver/Event 保留在排除清单）；实施期 re-grep 双语 latest 其余文件命中即同步。`_disclosure.py` effects 不含两项措辞（已核实），预计不改——实施期核对 `tests/test_lazy_disclosure.py` 漂移断言。`semantic-layer.mdx` 无 distinct/quantile 能力句（grep 核实为 authoring 语义），预计不改。
7. **整体验证与验收文档**。§6 全量命令；验收文档按实际验收日落盘 `docs/superpowers/specs/YYYY-MM-DD-multisource-capability-c7-acceptance.md`，逐后端×形态单元矩阵 + 探测引擎事实 + 阻塞记录 + 提交链回执，不覆盖 `multisource-slice-N` 记录。

## 6. 验证命令

```bash
# 准入与方法闭包（G1 + 负向守护）
make test TESTS='tests/test_lazy_scalar_admission.py tests/test_lazy_group_b_admission.py tests/test_lazy_backend_dispatch.py tests/test_lazy_status_fold_admission.py tests/test_lazy_postgres_admission.py'
# 私有状态编译与本地 oracle（G7）
make test TESTS='tests/test_lazy_distinct_compiler.py tests/test_lazy_distribution_compiler.py tests/test_lazy_distinct_alignment.py tests/test_lazy_distinct_fold.py tests/test_lazy_distinct_numeric.py tests/test_lazy_distribution_numeric.py tests/test_lazy_distribution_failures.py tests/test_lazy_distribution_topk.py tests/test_public_quantile_input.py'
# 运行时 oracle（G7 runtime；DuckDB 不改动，全绿守护）
make runtime-test TESTS='tests/test_lazy_distinct_runtime.py tests/test_lazy_distribution_runtime.py tests/test_lazy_distinct_runtime_acceptance.py tests/test_lazy_distribution_runtime_acceptance.py' RUNTIME_WORKERS=1
# 逐后端 R（服务可用时串行；opt-in 变量以各测试文件头部既有写法为准）
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py' RUNTIME_WORKERS=1
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py' RUNTIME_WORKERS=1
make runtime-test TESTS='tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py' RUNTIME_WORKERS=1
# 语句统计与传输守护（新增角色后核对）
make test TESTS='tests/test_lazy_statement_statistics.py tests/test_lazy_scalar_transport.py'
# 静态与整门禁
make typecheck TYPECHECK_TARGETS='marivo/analysis marivo/semantic'
make lint-agent LINT_TARGETS='marivo/analysis/operators tests/test_lazy_postgres_methods.py tests/test_lazy_mysql_methods.py tests/test_lazy_sqlite_methods.py tests/test_lazy_trino_methods.py tests/test_lazy_clickhouse_methods.py tests/test_lazy_distinct_compiler.py tests/test_lazy_distribution_compiler.py'
make check-agent
git diff --check
# 披露（site 改动时）
npm --prefix site run verify:content && npm --prefix site run build
```

数据库组串行，先 `bash tests/multisource_environment/manage.sh status <service>` 确认；缺服务或探测不成立导致某单元未验收时记录阻塞，C7 状态保持未完成。不运行 release-check、不启动 MinIO。

## 7. 完成条件

- §1 两项交付在 §5 锁定顺序内逐后端有真实执行回执、独立手算期望值与负向邻接项全批通过；探测不成立的后端×形态单元在验收矩阵记为剩余拒绝并附引擎事实。
- §3.1 各 gate 的解除有准入级测试覆盖（接受/拒绝两侧）；翻转的既有负向（dispatch median、PG median 钉）在验收文档逐条列出；percentile fold 拒绝钉保持通过。
- 私有状态边界有旅程级证明：原始 key/值不进入 Arrow payload、Evidence、metadata 与 statement inventory（`assert_no_raw_keys`/`guard_*_transport` 移植到远程旅程）；generic retained 迁移拒绝保持。
- 披露（analysis spec、双语 site、命中时的漂移断言）同批更新且预算测试通过。
- `make check-agent` 通过；`git diff --check` 干净。
- 验收文档落盘并链接证据；未验收单元明确列出，不标 C7 完成。

## 8. 排除项

percentile/quantile status-time fold（五后端拒绝保持）；`duckdb_tdigest@v1` 远程化；sampling、Entity correlation/candidate、driver、隐藏轴扩展归因（C8）；Event/Lifecycle（C9）；跨 datasource 联邦、远程 retained 上传、任意 SQL 入口；`_distinct_expansion_spine` 隐藏轴准入边界扩大；computed measure 键/值；`supported_distinct_key_type` 词表扩大；表形态扩展；`FINAL`/去重类缓解（ClickHouse 铁律沿用）。DuckDB 不改动。packaged skills 只读排查，如需修改另获用户批准。本阶段不启动 C8–C10；C0/总计划与本计划冲突处按总计划 C7 行与本计划 §2/§5 的顺序决策执行，偏差记入验收文档。
