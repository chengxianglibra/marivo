# C0：基线核验与后续实施计划清单

日期：2026-09-16。状态：C0 已执行，结果见 [C0 验收](2026-09-16-multisource-capability-c0-acceptance.md)。

## 1. 本阶段范围

依据[总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)，仅核验当前能力、环境和历史证据，锁定第一批目标并修正旧进度摘要。基线为 `7ddbe940de5c379f6944182114a7278ed61ef368`，开始时工作区干净。

本阶段不改变生产代码、公共 API、Store、依赖、测试或 packaged skills；不创建数据库 fixture，不启动/停止服务，不提交、推送或发布。代码准入、历史源码验收、历史 wheel 验收和本次环境探测分别记账。C0 完成不授权或表示 C1–C10 已实现。

执行顺序：核对 registry/support/adapter 与 owner spec；核对 Slice 3–8 历史证据；读取专用环境状态并使用现有 reader 做只读探测；运行约定静态检查；写入矩阵与清单；校验链接和 diff。

## 2. 第一批目标与阶段进入条件

第一批为 C1、C2、C3a、C4，加上经独立计划锁定的 C5 单元，最后 C10 有界安装包验收。精确后端目标见 C0 验收的目标矩阵。C5 已按用户要求锁定 ClickHouse Distributed 与 Trino 不限制 catalog/connector 类型，均为首批必交目标；真实多分片环境和代表性非 Iceberg catalog 尚待准备，不能据此宣称 C5 已完成或排除这两个目标。C3b、C6–C9、T3 正向复杂值计算不计入第一批。

下面是实施计划清单，不是提前授权的实现方案。每阶段开始前新增 `YYYY-MM-DD-multisource-capability-cN-implementation-plan.md`；C3a/C3b 分开。计划必须列出精确后端/方法/类型/输入形态、文件与现有符号、fixture、独立预期值、负向邻接项、完整命令及排除项。新符号在该阶段决定，不在 C0 虚构。

路径以下均相对仓库根目录。表中的 owner 是探索起点；不得仅删除准入限制而跳过下游验证。

| 阶段 / 前置 | 目标与代码拥有者 | 复用 fixture / 测试入口 | 执行前必须补齐的决策 |
| --- | --- | --- | --- |
| C1 / C0 | 六后端声明表源统一列依赖契约，包含 DuckDB；按 Entity、精确 binding 与物理关系收集完整依赖；`compiler/normalize.py::required_entities`、`operators/scalar_support.py::unsupported_reason`、`materialization/scalar_sql_execution.py::ScalarExecutionAdapter.prepare_dataset`、`postgres_execution.py::PostgresExecutionAdapter.get_schema`、`duckdb_execution.py::DuckDBExecutionAdapter.get_schema` 与 Runtime `_validate_source_schema`/`_declared_table`（上述目录均在 `marivo/analysis/`，Runtime 入口位于 `materialization/admission.py`） | `tests/lazy_execution_fixtures.py::make_execution_registry`、`tests/lazy_scalar_source_fixtures.py::registry_for`、`tests/lazy_postgres_fixtures.py::registry_for`；G1、R | 依赖事实的唯一类型/拥有者、关系身份、sidecar 表达式解析及未知表达式拒绝；覆盖隐藏 measure、主键、关系键、版本轴、parts；区分已声明未使用列与未声明物理列，统一缺列/不支持类型/类型不匹配的结构化诊断，保留上游 load/readiness 契约 |
| C2 / C1 | 按目标矩阵扩展标量映射、精确传输和基础方法；`marivo/datasource/engines/`、各 `operators/*_support.py`、各 `materialization/*_execution.py::get_schema` 和流式转换 | 上述 scalar/PostgreSQL fixture、各 `tests/test_lazy_*_runtime.py`；G1、R | 逐物理类型的映射、NULL/比较/排序、UInt64 极值及溢出、Arrow/Parquet/冷读；SQLite 约定存储是否合法须先由语义 owner 确认；timestamp 不自动获得时间轴准入 |
| C3a / C2 与时间契约 | timestamp 时间轴、小时/日桶、明确时区/DST、六后端统一时区策略与实际提交记录；`marivo/analysis/compiler/source_time.py`、`temporal.py::bucket`、`operators/scalar_support.py`、具体 adapter；遵循 `docs/specs/temporal-semantics.md` | `tests/lazy_temporal_fixtures.py::temporal_fixture`、各后端 methods fixture；G3、R | 每后端 instant/civil 映射、精度与时区范围、输出桶坐标、gap/fold 和截断拒绝；先验收所需 T2 类型，不能把 C2 timestamp 传输当成时间方法证据；确定时区失败/fallback 策略、来源记录和提交事件口径，见下节 |
| C3b / C3a 与独立计划 | 既有解析声明及多单位桶；相同时间 owner 与 semantic validator | temporal fixture；G3、R | 明确 parser、格式、模糊/非法值处理、桶单位与 count；不混入日历/累计；不计第一批 |
| C4 / C1、所需 C2 类型 | 受限行表达式、Linear graph、结果精度可解析 Decimal；`marivo/semantic/validator.py`、`metric_graph.py::LinearNodeV1`、`marivo/analysis/compiler/lowering.py`、scalar support 与 adapter | execution/scalar/PostgreSQL fixture、各 methods fixture、`tests/lazy_retained_fixtures.py`；G4、R | 逐表达式白名单与输入依赖、每步 precision/scale、除零/溢出、retained components 方程；后端不能保持精度的单元保留拒绝，禁止 float 降级 |
| C5 / C0、所需类型 | ClickHouse Distributed、Trino 无 catalog/connector 类型白名单；其他表形态待选；各 adapter `get_schema` 及 datasource engine 元数据 owner | 各后端 methods/runtime fixture；G1、R | Distributed 多分片拓扑、跨分片身份/聚合/fanout/故障；Trino 通用元数据路径、实际类型/表达式/只读校验、Iceberg 与非 Iceberg 验收样本，样本不成为白名单；PostgreSQL 先补视图/分区证据；不得默认 FINAL 或去重 |
| C6 / C3、C4 所需状态 | 日历、累计、status-time fold、剩余 validity；`compiler/temporal.py::cumulative_start`、`lowering.py` 和 temporal owner | temporal/retained fixture；G3、G4、R | 各子能力与后端顺序、空桶/端点/重叠/跨期规则及 retained 状态；不计第一批 |
| C7 / 类型与私有状态设计 | 先 exact distinct，再 quantile/distribution；`compiler/distinct.py::membership_validations`、`distribution.py::source_quantile`、`materialization/distribution.py`、registry | `tests/lazy_distinct_fixtures.py`、`tests/lazy_distribution_fixtures.py`；G7、R | 最小首发后端、membership/分布状态、重叠组 rollup、数值算法与传输权限；禁止丢弃续算状态或用近似替代既有精确定义 |
| C8 / 各准备路径可行 | sampling、Entity correlation/candidate、driver、扩展归因分别计划；`materialization/sampling.py`、`compiler/correlation.py::prepare_pairs`、`entity_candidate.py::lower_entity_candidate`、`driver_candidate.py`、`attribution.py` | `tests/lazy_correlation_fixtures.py`、`tests/lazy_entity_candidate_fixtures.py`、execution fixture；G8、R | 各方法首发后端、只读单次求值机制、身份/配对/隐藏轴、私有状态流向；无法证明则保持拒绝，不能远程上传或搬原始身份绕过 |
| C9 / 排序、时间、准备能力 | Event 匹配后 Lifecycle 重放；`compiler/event.py::compile_event_match`、`lifecycle.py::compile_replay` 及 publication owner | `tests/lazy_event_fixtures.py::make_event_registry`、`tests/lazy_lifecycle_fixtures.py::lifecycle_registry`；G9、R | 首发后端、并列/乱序/重复/边界、重放完整性和单次求值；不计第一批 |
| C10 / 本批锁定单元全部验收 | 最终 wheel、冷进程、负向矩阵及剩余差距；`tests/installed_multisource_probe.py`、`tests/test_installed_multisource.py`、Makefile 安装包入口 | installed probe 和专用环境 helper；G10 | 最终目标单元清单、wheel hash/导入来源、服务串行安排、各阶段 receipt 链接、独立 driver 观察与 Runtime SQL/角色/计数核对；必须覆盖已锁定的 Distributed 与 Trino 非 Iceberg 旅程；C5 其他未选单元明确排除，不标完成，不以安装验收替代方法专项验收 |

### C1 已确认的实施约束

- 唯一依赖事实由 compiler/normalization 产生；保留 Entity 与物理关系归属，关系身份包含精确 datasource binding、catalog/schema/table。不同 Entity 指向同一物理关系时仍保留各自语义绑定，不按裸列名混合。
- 静态准入、`ExecutionAdapter` 的 schema 请求、Runtime `_validate_source_schema()`、`_declared_table()` 和实际源投影使用同一事实。独立 C1 计划须确定内部具体类型与显式传递接口，不能依靠 adapter 私有全局列集合重算一套依赖；不新增公共 API。
- PostgreSQL 和 DuckDB 的独立 `get_schema()` 与四后端共享路径都须接入；元数据 SQL、类型映射、游标和传输方式仍各归具体 adapter。统一行为不要求合并继承层级。完整元数据描述可以读取，但无关列必须在类型解析前排除；未知必要依赖或必要列缺失仍结构化失败。
- 测试同时覆盖已声明但未使用的复杂列、未声明的无关物理类型、跨关系同名列、逻辑列到物理列重命名、隐藏指标、仅供身份/关系/版本断言使用的列，以及空输出下的必要断言；验证生成 SQL 投影，不能仅断言 schema 集合变小。
- DuckDB 声明表源纳入统一行为及回归。不得缩小其既有复杂类型、文件源、sampling、Event/Lifecycle、临时资源或 retained 能力；既有合法依赖必须完整建模，不能借未知依赖拒绝让已支持方法退化。新远程类型与 C5 表形态仍由后续阶段激活。

### 统一诊断、时间与回执的实施约束

| 归属阶段 | 改动与拥有者 | 必须新增的验收场景 |
| --- | --- | --- |
| C1：schema 诊断 | `materialization/admission.py::DatasetRuntime._validate_source_schema`、`materialization/errors.py::MaterializationError` 与 adapter 元数据错误；使用统一依赖中的关系/列身份与实际类型，不建立另一份字段清单 | 缺列、声明/实际类型不同、不支持物理类型各有可区分原因；展示正确 Entity/关系、逻辑列/源列、预期/实际类型与可执行修复；同名列不串关系，禁止输出凭据/业务值 |
| C3a：时区策略 | `marivo/datasource/timezone.py`、现有 temporal owner、六后端 `timezone()` 与 Runtime 解析消费路径；物理探测归 adapter，优先级/fallback/失败与 provenance 归共同策略 | 对相同时间事实和失败分类比较六后端语义结果；覆盖无探测能力、探测异常、非法名称、固定偏移/IANA、显式 parser、主机 TZ 变化、DST；冷读复用持久化事实，不重新解释；现有合法路径不退化 |
| C3a：实际执行记录 | `materialization/execution.py::ExecutionAdapter`、各 adapter 提交路径与 `admission.py::_record_statement`；移除 Runtime 预记 profile SQL，统一实际文本/角色与计数来源 | Trino/ClickHouse 时区 SQL 与实际提交逐字一致；metadata/validation/primary/parts/本地续算不漏记或重复；无探测 SQL、编译失败、提交失败与成功分别验证；保留原始异常；驱动内部未观测部分明确标注 |
| C10：独立回执验收 | installed probe、环境观察器与各阶段 receipt；复用 C3a 记录契约，不另建运行时能力/语句清单 | 独立 driver 边界捕获与 Runtime 回执逐条核对 SQL、角色、数量及结果状态；区分 source 与 local 语句，验证零源查询的冷命中；非 Iceberg 与 Distributed 纳入最终样本 |

各独立阶段计划须补齐内部接口、具体错误字段/原因及新增测试归属，先与现有 owner 契约对齐，再实现。时区统一不预设“探测失败一律 fallback”；执行记录统一不新增重试、查询预算或远程终止证明。必要的后端游标、binary RECORD、标量展开、数值转换、取消和资源生命周期实现继续保留。

## 3. 可执行验证入口

以下 G/R 命令是后续计划可复用的现有入口，**C0 没有运行这些后续阶段门禁**。新增案例和精确 `-k`/文件选择须由对应阶段计划补齐；现有基线测试通过不能证明新增单元。修改测试前必须读取 `marivo-test-fixtures` skill。fixture 的管理员准备和 reader 执行继续分离。

```bash
# G1: dependency admission and execution ownership
make test TESTS='tests/test_lazy_scalar_admission.py tests/test_lazy_postgres_admission.py tests/test_lazy_backend_dispatch.py tests/test_lazy_scalar_execution_adapter.py tests/test_lazy_duckdb_execution_adapter.py'
# C1: shared Runtime and preserved DuckDB table/retained behavior
make runtime-test TESTS='tests/test_lazy_materialization_execution.py tests/test_lazy_retained_runtime.py' RUNTIME_WORKERS=1
# G3: temporal semantics baseline
make test TESTS='tests/test_lazy_temporal_source.py tests/test_datasource_runtime_timezone.py tests/test_analysis_session_timezone.py tests/test_lazy_statement_statistics.py tests/test_lazy_backend_dispatch.py'
# Shared diagnostics and transport baseline; new adversarial cases are added by C1/C3a
make test TESTS='tests/test_lazy_postgres_errors.py tests/test_lazy_scalar_transport.py'
make runtime-test TESTS='tests/test_lazy_temporal_runtime.py tests/test_lazy_temporal_public_runtime.py' RUNTIME_WORKERS=1
# G4: numeric and retained baseline
make test TESTS='tests/test_lazy_retained_fold_matrix.py'
make runtime-test TESTS='tests/test_analysis_decimal_e2e.py tests/test_lazy_retained_runtime.py' RUNTIME_WORKERS=1
# G7: private membership and distribution baseline
make test TESTS='tests/test_lazy_distinct_compiler.py tests/test_lazy_distribution_compiler.py'
make runtime-test TESTS='tests/test_lazy_distinct_runtime.py tests/test_lazy_distribution_runtime.py' RUNTIME_WORKERS=1
# G8: identity and pair preparation baseline
make test TESTS='tests/test_lazy_population_sampling.py tests/test_lazy_correlation_compiler.py tests/test_lazy_entity_candidate_compiler.py'
# G9: event and replay baseline
make test TESTS='tests/test_lazy_event_compiler.py tests/test_lazy_lifecycle_numeric.py'
make runtime-test TESTS='tests/test_lazy_event_runtime.py tests/test_lazy_lifecycle_runtime.py' RUNTIME_WORKERS=1
```

R：对应后端的 methods/runtime 入口（各自服务已准备时执行；重型检查串行，不在本阶段调用环境 start）：

```bash
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py' RUNTIME_WORKERS=1
make runtime-test TESTS='tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py' RUNTIME_WORKERS=1
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py' RUNTIME_WORKERS=1
```

涉及生产 Python 的阶段，独立计划必须按实际修改文件给出 `make typecheck TYPECHECK_TARGETS='...'` 和 `make lint-agent LINT_TARGETS='...'` 的完整参数，共享行为最终执行 `make check-agent`。披露激活遵循总计划第 4 节，显式列出 Help、动态指导、漂移/可达性/预算测试及中英文文档；packaged skill 如需修改须另获用户明确批准。普通阶段不运行 release-check 或启动 MinIO。

G10：构建命令为 `make pypi-build pypi-check`。安装验证复用[现有 runbook](../../../tests/multisource_environment/README.md#slice-8-installed-package-acceptance)的依赖与 origin/hash 守卫，在独立计划锁定服务时序后执行：

```bash
MARIVO_INSTALLED_MULTISOURCE_TEST=1 MARIVO_INSTALLED_BACKENDS=sqlite,postgres,mysql,clickhouse MARIVO_MULTISOURCE_EVIDENCE_DIR=/tmp/marivo-capability-c10 make installed-multisource-test
MARIVO_INSTALLED_MULTISOURCE_TEST=1 MARIVO_INSTALLED_BACKENDS=trino MARIVO_MULTISOURCE_EVIDENCE_DIR=/tmp/marivo-capability-c10 make installed-multisource-test
```

## 4. C0 验收

本次命令、环境观察、精确矩阵和未验证项统一收录于 [C0 验收](2026-09-16-multisource-capability-c0-acceptance.md)，避免本文件成为第二份当前能力表。C0 完成条件是证据可追溯和计划入口明确，不要求提前完成后续阶段的设计决策或真实能力验收。
