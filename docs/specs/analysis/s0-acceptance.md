# Marivo Analysis DSL S0 验收与 S1 交接

本文件固定保存 S0 当时的验收快照；后续 W1/W2 结果见
[S1 实施记录](s1-implementation-record.md)。下文的“当前”均指 S0 快照时间，
不表示当前工作树的执行状态。

Date: 2026-09-24

Status: **通过（仅 S0 契约与接入）**。本记录不表示公开 DSL、J1–J4、真实双生产者、冷恢复或真实 Agent 已验收。

## 1. 验证快照与证据解释

本次验证在 `panda` 分支、2026-09-24 12:05–12:08 UTC 的本地工作树进行。开始时 `HEAD` 为
`fb827a9581fb832aea479f2ebedd3d560785287e`，接口设计文档有一处未暂存修改，暂存区为空。
验证期间该文档以 `a4757f2b31f9b34a2d67510dbc7576bfadf4f5ea` 提交；提交前后下表所列
文档内容 SHA-256 不变，`git diff fb827a9581..a4757f2b31 -- marivo tests pyproject.toml`
为空。最终 `HEAD` 为 `a4757f2b31f9b34a2d67510dbc7576bfadf4f5ea`，创建本记录前工作树
与暂存区均为空。下文每个 A 项和验证层均引用这一代码快照 **B1**；它们的结果只覆盖列明的命令。

| B1 输入 | SHA-256 或版本 |
| --- | --- |
| S0 实施计划 | `46f26df6810480f7f3567a765cb8c694d61a6f41f42f9ec8a7c27875c287b0ed` |
| MVP 验证计划 | `1451e417b764ac7a0944f3d72279844d24c94dd92d7284c4ff13c3bb7d34e6ac` |
| 接口设计 | `c8443c60712eaad80674d5908b202ce2a57a1efc6e447aeddd1d68f72e21a01b` |
| 架构设计 | `ba6149eb5aac04b803c5169a70440d70021fce79a9fa64dd2b55a3a3581f487c` |
| 依赖约束 `pyproject.toml` | `b2cd14ca3775f88d30de04aaa19cde8832ee3f8a7be191286eaf1bcd800e30a9` |
| Fixture F1：依次对 `tests/conftest.py`、`tests/shared_fixtures.py`、`tests/test_analysis_dsl_fixtures.py` 运行 `shasum -a 256`，再对结果运行 `shasum -a 256` | `5d7e2c0f2c02203ae2cd26fddcd948378fe5d4e9e04922d5f203bdf54f42fafb` |

F1 的单文件 SHA-256 依次为 `af003fc787ee624c248bed81426bdd497798aecf06057a9781a2314981008ce3`、
`cfc0182a1617b2ad5980ba74a80f1dfc8e4d9dbc0019246413b835bb382c1344`、
`52994225692bf26075729f8fddadc543d01e4e9cb2f00bf9d41455a14118a563`。
本地 `.venv`：Python 3.12.13；Marivo 0.5.3.dev0；Ibis 12.0.0；pandas 2.3.3；
PyArrow 25.0.1；DuckDB 1.5.3；NumPy 2.4.6；SciPy 1.17.1；pytest 9.0.3；
mypy 2.3.0；Ruff 0.15.15；Sphinx 9.1.0。非敏感配置：本地 DuckDB fixture，
报告时区 UTC，每例隔离 `tmp_path`、Session 和来源文件；pytest 默认使用 xdist，
`pytest.ini` 排除 `runtime` 与 `release` 标记。未启动远端服务或 MinIO。

## 2. 实测记录

每条记录均使用 B1、F1 和上述依赖/配置。状态仅取**通过、失败、未验证、阻塞**。
本次没有失败，故没有失败复现；如需复核，按表中原命令重跑，并以所列文件或检查阶段定位。
命令输出已在本次验证中观察，表内给出可复现的证据位置，不将未运行的阶段记为通过。

| ID / 层 | 命令 | 状态与结果 | 证据位置及复核入口 |
| --- | --- | --- | --- |
| C1 / 纯契约、交换、Store、fixture | `make test TESTS='tests/test_analysis_dsl_contracts.py tests/test_analysis_dsl_exchange.py tests/test_analysis_dsl_execution_identity.py tests/test_analysis_dsl_fixtures.py'` | 通过：44 passed | 四个同名测试文件；测试名分别定位 A02–A05、A07–A12 的私有接缝与独立 oracle。|
| C2 / 既有回归 | `make test TESTS='tests/test_lazy_dataset_registry.py tests/test_lazy_materialization_codec.py tests/test_lazy_materialization_store.py tests/test_public_surface.py'` | 通过：119 passed | 四个同名测试文件；覆盖 registry、既有 codec/Store 和公开面未被误改。|
| C3 / 类型 | `make typecheck TYPECHECK_TARGETS='marivo/analysis tests/typing/analysis_dsl_internal_contract.py'` | 通过：220 source files，无问题 | `tests/typing/analysis_dsl_internal_contract.py` 与 `marivo/analysis/`。|
| C4 / lint | `make lint-agent LINT_TARGETS='marivo/analysis tests'` | 通过：693 files already formatted，Ruff 与 import contracts 通过 | `Makefile` 的 `lint-agent` 目标。|
| C5 / 全量默认门禁 | `make check-agent` | 通过：lint、361 source files 的 typecheck、5067 passed / 4 skipped、API docs built | `Makefile` 的 `check-agent` 及 `docs-api-agent` 目标；默认测试不包含 Runtime/release。|

文档接受由已提交的 [Analysis owning spec](python-analysis-design.md#accepted-s0-analysis-dsl-slice-inactive)、
[方法规则](operators-and-frames.md#accepted-s0-method-rules-inactive) 和
[Runtime owning spec](session-state-and-runtime.md#accepted-s0-input-and-execution-protocol-inactive)
交叉核对，状态为**通过**。这不是执行测试。T5 测试中的 J1–J4 SQL/算术是独立 oracle，
状态为**通过（fixture）**；J1–J4 DSL 运算仍为**未验证**。

## 3. A01–A12 owner 差距清单

“S0 状态”只评价已接受的私有契约、低层接缝或 fixture；同一行的生产接入义务列在最后一列。
证据 ID 均引用第 2 节的同一 B1/F1、测试命令、版本、配置及失败复核办法。

| ID / 需求 | 实际代码 owner | S0 状态 / 证据 | 当前差距与后续阶段 |
| --- | --- | --- | --- |
| A01：Entity、时间角色、Metric 根及值政策 | `marivo/semantic/metric_graph.py`；`marivo/semantic/metric_graph_lowering.py` | 通过：真实声明及图可得根/组件，待执行检查未升级为证据；C1 `test_real_metric_graph_derives_components_but_not_missing_authority`、`test_real_declarations_load_with_distinct_roots_and_pending_authority` | S1 的 Semantic 声明 owner 须补齐并版本化贡献划分、可加性与值政策；相应消费者准入前完成，不能从函数体或 fixture 布尔值推断。 |
| A02：域、成员与贡献坐标、显式节点 | `marivo/analysis/datasets/descriptors.py`；`handles.py` | 通过：封闭私有类型及节点身份；C1 `test_domain_member_identity_and_contribution_coordinates_are_distinct`、`test_input_classification_stops_at_artifact_and_keeps_explicit_node_identity`，C3 | S1–S2 将其接入实际构造和执行；当前没有公开 DSL 结果路径。 |
| A03：六项基础能力、RequiredParts 与局部保持 | `marivo/analysis/operators/registry.py` 的 `MethodContract`；方法规则 owning spec | 通过：封闭方法输入、输出、部件及 Cell/数值政策校验；C1 `test_method_semantics_are_single_owner_for_qualified_implementations`、C3 | S1–S2 实现方法生产者和真实部件变换；静态规则不证明数值算法已执行。 |
| A04：方法语义与实现资格分离 | `marivo/analysis/operators/registry.py` | 通过：隔离注册与精确路线拒绝；C1 `test_method_semantics_are_single_owner_for_qualified_implementations`、C2、C3 | S1 仅在有真实实现及证据后注册来源 Ibis 和 pandas 路线；当前无新生产能力注册。 |
| A05：传递来源/Artifact/混合分类 | `marivo/analysis/compiler/normalize.py` | 通过：物化叶停止、显式来源才混合、纯分类无业务 I/O；C1 `test_input_classification_stops_at_artifact_and_keeps_explicit_node_identity`、`test_event_source_fact_prevents_artifact_only_false_negative` | S1–S2 将混合拒绝接到新切片准入边界；现有 `execute()` 不全局改语义。 |
| A06：来源 Ibis 与保留输入 pandas | `marivo/analysis/compiler/placement.py`；现有 execution registry | 未验证：S0 只接受路线契约；C1/C2 不运行新双适配器 | S1 构造 Ibis 表达式并由 Ibis 编译 SQL；受控 Artifact 读取后只走 pandas。需真实双生产者与断源、禁 DuckDB scan 验收。 |
| A07：交换 schema、Cell、部件及检查 | `marivo/analysis/materialization/contracts.py`；`reads.py`、`storage.py` | 通过：私有 `marivo.analysis_exchange/v1` 往返、四 Cell、类型/绑定拒绝；C1 `tests/test_analysis_dsl_exchange.py`、C2、C3 | S1 接完整 codec 发布链与真实生产者；解码不授予已完成检查或 Artifact 发布资格。 |
| A08：稳定定义与每次来源 Run/key | `marivo/analysis/materialization/execution_key.py` | 通过：来源键含 Run，固定键含确切 receipt/版本，混合拒绝；C1 `tests/test_analysis_dsl_execution_identity.py`、C3 | S1 在新 Runtime 分派中落实 writer guard、协调、lookup、Run 分配、admit 的顺序；尚无同一 Lazy 对象重复来源求值验收。 |
| A09：唯一发布、精确恢复、不自动重放 | `marivo/analysis/materialization/store.py`；后续消费者 `dataset_publication.py`、`reconciliation.py` | 通过：低层 Store 两个来源键、失败保护及原 Run read-back；C1 `test_store_keeps_two_exact_source_results_and_failed_retry_cannot_replace_them`、`test_lost_publication_acknowledgement_reads_back_the_original_run`、C2 | S1 贯通新协议的 Runtime/descriptor/receipt/Artifact 发布与冷恢复；固定输入竞争命中和未终结 Run 为集成义务。 |
| A10：真实声明与独立 J1–J4 oracle | `tests/conftest.py`；`tests/shared_fixtures.py`；`tests/test_analysis_dsl_fixtures.py` | 通过：真实加载、独立 SQL/算术、变名与边界数据；C1 其中 15 个 fixture 用例，F1 | S1–S3 将同一 fixture 用于 DSL 结果对照；目前没有 J1–J4 运算通过的证据。 |
| A11：同 Entity、无 lag Spearman 规则 | `marivo/analysis/operators/association_contracts.py`；方法规则 owning spec | 通过：S0 方法政策已接受、J4 独立秩 oracle 存在；C1 `test_j4_pairing_and_independent_rank_arithmetic` | S3 接 Relation/Cell 输入、配对状态、Python 数值核和交换/发布；真实 J4 未验证。 |
| A12：共同流 schema、完成检查与 close | `marivo/analysis/materialization/execution.py`；`reads.py` | 通过：同 schema 的内存与受控 Parquet 向量、空流、提前关闭和失败；C1 `tests/test_analysis_dsl_exchange.py` | S1 用真实 DuckDB 来源 producer 与本地 producer 验证资源所有权、全耗尽检查和发布原子性；S0 向量不代替它。 |

## 4. 分层结论与 S1 输入

| 验证层 | 状态 | 结论 |
| --- | --- | --- |
| 已接受文档切片 | 通过 | 域、Cell、组件、方法、Run/key 先后及单一 owner 在 owning specs 中确定；目标接口其余部分仍为 proposed。 |
| 私有契约和独立 fixture | 通过 | A02–A05、A10 的静态与纯构造证据成立；没有公开 DSL 调用资格。 |
| 最小 Store/codec/流接缝 | 通过 | A07–A09、A12 的低层往返、拒绝、身份与唯一性证据成立；未证明完整发布链。 |
| 真实来源与 pandas 双路线 | 未验证 | S1 负责 Ibis lowering、真实流、受控 pandas 续算、重复来源求值、固定命中和精确恢复。 |
| 真实 Agent 与公开披露 | 未验证 | S4 负责 Help、公开类型、真实 Agent 及完整矩阵。 |

S0 的关闭依据是：A01–A12 均有 owner 和阶段归属；已接受切片的域/Cell/组件含义及
Run/key 顺序没有悬而未决项；C1–C5 全部通过。A01 所缺**新声明元数据**及 A06
**新双路线实现**是明确的 S1 准入前置项，并非把未知语义默认为通过。S1 首先用 F1
完成 J1、真实双生产者、同一 Lazy 对象重复来源求值、固定输入精确命中与受控恢复；
随后 S2 接 compare/J3，S3 接 J4 数值核和完整边界，S4 接公开披露与真实 Agent。
后续结果汇入 MVP 规定的 `composition-acceptance.md`，本记录不代替该矩阵。
