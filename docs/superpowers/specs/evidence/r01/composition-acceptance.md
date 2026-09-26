# Marivo Analysis DSL 组合验收记录：S1 W5 与 S2 交接

Date: 2026-09-25

Status: **S4 P4 首轮适用公共必要项通过；其他真实后端、远端资源与发布未验证。**
S1/S2/S3 历史结论保留在 §1–§14；S4 P3 安装包用户脚本与真实 Agent
记录在 §15，S4 P4 公共矩阵与交付判定记录在 §16。

## 1. 固定输入与复现环境

本次在干净的 `panda` 工作树验证；实施代码 `HEAD` 为
`5766bdc7580132e48f9f5b8965480ed63c389f00`（W4 提交）。以下结果均指向此代码
快照，W5 只新增本文档并更新 S1 工作计划。S0 验收是较早快照，不能替代本次运行。

| 输入 | 版本或 SHA-256 |
| --- | --- |
| MVP 验证计划 | `421f8d855fcfa994bb0279b545faf1244de391f8ae8579ba50158a885adced04` |
| 接口设计 | `319223cf12e7c9b53b0b5bda5c0d7b2e558b4e372e48534e072eb53eed39365a` |
| 架构设计 | `ba6149eb5aac04b803c5169a70440d70021fce79a9fa64dd2b55a3a3581f487c` |
| Analysis owning spec | `de8dd72e1589bb6d6f0d12ec1e2b2311b77c31af999e1712a9c2a60289df51d5` |
| 方法 owning spec | `d39d8e404a856f02d92b0f98ddaaa74ab7dbb263683859832e4197bddf07b5e8` |
| Runtime owning spec | `45e5c251a9fa4729fd74f6c4b19f2e7b342e80b6bcccac16103a947f3781c8b7` |
| 依赖约束 `pyproject.toml` | `b2cd14ca3775f88d30de04aaa19cde8832ee3f8a7be191286eaf1bcd800e30a9` |
| Fixture F2：`tests/conftest.py` | `389847e76ad77f13d496cbb04b18893559036292148bb7da3cc6c6be5a226b9f` |
| Fixture F2：`tests/shared_fixtures.py` | `b8d49a5798240745fe5c91e7cb28988ba9b4a08e4f18eee6ba19f4aa76f83f0e` |
| Fixture F2：`tests/test_analysis_dsl_fixtures.py` | `aaee341a7152d268e46513c5887cb23e09b625cdcd85aae1f84af358688e6676` |

F2 的 `analysis_dsl_rows("j1")` 固定四个 Customer：A/B 属 east、C 属 south、D 属
west。八月三笔有效订单分别为 A/web=450、B/mobile=150、C/web=400；D 无订单。
七月及九月边界订单用于半开时间窗口反例。独立 oracle 在
[`test_analysis_dsl_fixtures.py`](../../../tests/test_analysis_dsl_fixtures.py)；J2–J4
在该文件中的通过只表示 fixture 正确，不表示 DSL 执行。

本地 `.venv`：macOS、Python 3.12.13、Marivo 0.5.3.dev0、Ibis 12.0.0、
pandas 2.3.3、PyArrow 25.0.1、DuckDB 1.5.3、NumPy 2.4.6、SciPy 1.17.1、
pytest 9.0.3、mypy 2.3.0、Ruff 0.15.15、Sphinx 9.1.0。非敏感配置：
本地 DuckDB 文件、UTC 时间输入、pytest `tmp_path` 隔离项目/Session/Store、
默认 xdist；`pytest.ini` 的默认测试排除 `runtime`/`release`。冷恢复子进程把
`MARIVO_PROJECT_ROOT` 指向隔离项目；未启动远端服务或 MinIO。

## 2. 实际运行与分层结论

状态只取**通过、失败、未验证、阻塞**。命令从仓库根目录执行；负例也包含在相应测试
文件中，可用表内命令或指定测试节点重跑。本次命令没有意外失败。

| 层 / 状态 | 命令和实际结果 | 证据与界限 |
| --- | --- | --- |
| 文档/类型：通过 | `make typecheck TYPECHECK_TARGETS='marivo/semantic marivo/analysis tests/typing/analysis_dsl_internal_contract.py'`：279 source files，无问题；`make lint-agent LINT_TARGETS='marivo/semantic marivo/analysis tests'`：756 files already formatted，Ruff/import contracts 通过 | 已接受的私有切片见 [Analysis](../../specs/analysis/python-analysis-design.md)、[方法](../../specs/analysis/operators-and-frames.md)、[Runtime](../../specs/analysis/session-state-and-runtime.md) owning specs；公开 Help/签名的 S4 验收仍未验证。 |
| Ibis 编译：通过 | 下列 160 项默认测试中的 J1 来源用例完成 `backend.compile(expression)` 后由 Ibis/DuckDB 执行 | [`source_stage.py`](../../../marivo/analysis/materialization/source_stage.py) 对检查、主表和部件表达式逐一委托 Ibis；[`dsl_j1_source.py`](../../../marivo/analysis/compiler/dsl_j1_source.py) 构造 Ibis 表达式。仅证明已接入 DuckDB/J1 形状，不证明 V15 的全部三路线情形。 |
| 真实 DuckDB/J1：通过 | `make test TESTS='tests/test_analysis_dsl_fixtures.py tests/test_analysis_dsl_contracts.py tests/test_analysis_dsl_exchange.py tests/test_analysis_dsl_execution_identity.py tests/test_analysis_dsl_j1_construction.py tests/test_analysis_dsl_j1_source.py tests/test_semantic_live_help.py tests/test_unified_help.py tests/test_agent_api_drift.py'`：160 passed | [`test_j1_ibis_source_matches_independent_oracle`](../../../tests/test_analysis_dsl_j1_source.py) 核对总收入 1000、east=600/south=400/west=Null、east 渠道 web=450/mobile=150、直接 Region 分组与同绑定 read→group 一致；D 的原状态保留空贡献。`customers.group_by(Channel)` 在构造期拒绝。此处执行的是私有 `J1Context`/`run_j1_source`。 |
| pandas 本地续算：通过 | 同一 160 项中来源/本地、receipt 和拒绝用例通过 | [`test_analysis_dsl_j1_source.py`](../../../tests/test_analysis_dsl_j1_source.py) 核对受控输入的 Channel 分组、原状态 rollup、当前行 count/sum/mean 的政策与拒绝；禁用 `duckdb.connect` 后仍能在 pandas 继续。物理执行不同，不要求浮点逐位相等。 |
| 交换/冷恢复：通过 | `make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py tests/test_analysis_dsl_j1_artifact.py'`：7 passed；默认 160 项含交换负例 | [`test_analysis_dsl_exchange.py`](../../../tests/test_analysis_dsl_exchange.py) 对真实 Ibis/Parquet 生产者及固定 schema、四 Cell、空流、精度、错序/漂移/损坏与 close 建立证据；[`test_analysis_dsl_j1_artifact.py`](../../../tests/test_analysis_dsl_j1_artifact.py) 验证部件 receipt 和断源新进程 Channel 续算。Mock close 不能替代其他后端资源资格。 |
| Runtime/原子性：通过 | 同一 7 项 Runtime 测试通过 | [`test_analysis_dsl_j1_runtime.py`](../../../tests/test_analysis_dsl_j1_runtime.py) 对同一 J1 对象连续执行：定义不变，A 值 450→500，两个 Run/key/Artifact 不同且断源精确恢复；固定输入重复命中不新增 Run，receipt/方法/协议变化不误命中；失败、回执丢失、待协调 Run、竞争与混合早拒绝均有用例。来源打开次数是两次顶层求值证据，不是运行内多消费者共享证据。 |
| 全量默认门禁：通过 | `make check-agent`：845 files already formatted，Ruff/import contracts 通过，370 source files 类型检查通过，5101 passed / 4 skipped，API docs built | 覆盖全库 lint、typecheck、默认测试和 API docs 构建；默认测试不包含 Runtime，上面的 `make runtime-test` 单独补足本阶段集成证据。 |

针对失败路径的复核入口：

- 来源型第二次失败与提交结果不明：`make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py::test_j1_runtime_failure_and_uncertain_publication_keep_the_first_artifact'`。
- 不可确定的对账不自动重放来源：`make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py::test_j1_failed_outcome_resolution_is_reported_without_source_replay'`。
- 固定成员加现场 read 在行读取前拒绝：`make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py::test_j1_runtime_rejects_fixed_member_plus_live_read_before_rows'`。
- 批次 schema 漂移、提前 close、取消和原生迭代失败：`make test TESTS='tests/test_analysis_dsl_exchange.py::test_schema_drift_unsorted_keys_early_close_and_native_failure'`。

## 3. S1 子集与完整验证矩阵的距离

“通过”只按本行的**S1 子项**解释；完整 V01–V15 仍按
[MVP §5](../specs/2026-09-24-marivo-analysis-dsl-mvp-validation-plan.md#5-验证目标正确性架构与代价)
逐项验收。

| 范围 | 状态 | 已证实及未证实的边界 |
| --- | --- | --- |
| J1 的私有数值、域、Cell 与单根状态（V01/V02/V03 子项） | 通过 | 上述 F2 固定夹具和双路线结果成立；独立 J2–J4 oracle 没有生产执行链。目标公开示例末端的 `group_by(Channel).rollup()` 未按原样运行；当前私有 `group_by(Channel)` 已得到两个渠道值，而来源适配对分组后 `rollup` 显式拒绝。不能把两种调用形状混写为已验收。 |
| 已接入原子能力的双路线（V05 子项） | 通过 | Channel 状态分组、Entity 原状态 rollup、当前行统计和拒绝向量有来源/本地对照；不推广到所有目标方法、类型或后端。 |
| 真实两类流、保存部件与 K（V06/V07 子项） | 通过 | 固定 schema、receipt、完整读取与新进程断源续算成立；未要求 J2 Difference、J4 AssociationResult 的 K。 |
| 来源身份、固定命中与失败原子性（V08/V10 子项） | 通过 | 同对象逐次来源求值、固定命中、精确恢复、失败保护与 reconcile 有实测。 |
| 一次执行图内共享显式节点（V10 子项） | 未验证 | 已准入 J1 节点均为单前驱，没有一次执行内让同一节点进入多个消费者的合法图。`opens == 2` 是两次顶层执行；缓存命中是固定输入复用，均不能替代此项。S1 总关闭须先补能区分重复读取的合法图和计数。 |
| 完整 V01–V15、J2–J4、成本与扩展（V01–V15 其余项） | 未验证 | compare、多根 ratio、Spearman、批次/1,000 与 100,000 事实成本、能力桩和完整拒绝矩阵尚未实施或测量。 |
| 公开类型/Help、脚本与真实 Agent（V14） | 未验证 | 私有 `DatasetRuntime.execute_j1` 不构成公开目标 DSL、真实 Agent 或 S4 披露验收。 |
| 非 DuckDB 来源、远端资源/取消资格 | 未验证 | 只有本地 DuckDB 来源和 pandas 固定输入执行；没有第二真实后端、远端服务或 MinIO。 |

因此，W5 的记录工作完成时仍应写 **S1 总验收未验证**。不能仅因 160+7 项测试
和全量门禁通过就关闭 V10 的共享义务，也不能让 S2 接手后把该缺口倒算成 S1 通过。

## 4. S1 改动位置与 S2 增量基线

以 S0 计划时的 `a4757f2b31f9b34a2d67510dbc7576bfadf4f5ea` 到本次代码
`5766bdc7580132e48f9f5b8965480ed63c389f00` 作范围基线：`marivo/semantic` 与
`marivo/analysis` 共 41 个变动文件，`git diff --numstat` 为 4405 行新增、142 行删除。
这是版本差异统计，含声明/适配及联动修复，不是执行成本或 S2 的边际改动。S2 应从
此 SHA 重新计数，明确哪些位置增加方法语义、哪些仅增加后端实现。

| 需求与适用能力 | 当前 owner / 实现位置 | 验证入口 | S2 扩展时的分界 |
| --- | --- | --- | --- |
| 正式 Metric 单位、可加性、时间和值政策；J1 Revenue sum | [`_authoring_declarations.py`](../../../marivo/semantic/_authoring_declarations.py)、[`_authoring_validation.py`](../../../marivo/semantic/_authoring_validation.py)、[`metric_graph_lowering.py`](../../../marivo/semantic/metric_graph_lowering.py) | `test_analysis_dsl_j1_construction.py`、`test_semantic_live_help.py` | J3 的多根组件与 ratio 规则从声明/规范图取得权威，不在 SQL 或 fixture 复制政策。 |
| 域、成员/贡献坐标、显式节点与 J1 构造 | [`dsl_j1.py`](../../../marivo/analysis/observation/dsl_j1.py)、`marivo/analysis/datasets/` | `test_analysis_dsl_j1_construction.py`、`test_analysis_dsl_contracts.py` | compare 应扩展有类型组合图；保留原域和同次实现绑定，勿把同形定义当同一实现。 |
| 方法版本、部件与 route 资格；六个 J1 数值注册（observe sum、group sum、rollup sum、当前行 sum/count/mean） | [`dsl_j1_contracts.py`](../../../marivo/analysis/operators/dsl_j1_contracts.py)、[`registry.py`](../../../marivo/analysis/operators/registry.py)、[`dsl_j1_values.py`](../../../marivo/analysis/operators/dsl_j1_values.py) | `test_analysis_dsl_j1_source.py` | J3 ratio 需自己的组件状态和 Cell 规则；不能复用当前行统计状态。新增方法先登记契约，再登记真实路线。 |
| Ibis 来源和 DuckDB 放置；成员、read、where、分组、sum/count | [`dsl_j1_source.py`](../../../marivo/analysis/compiler/dsl_j1_source.py)、[`placement.py`](../../../marivo/analysis/compiler/placement.py)、[`source_stage.py`](../../../marivo/analysis/materialization/source_stage.py) | `test_j1_ibis_source_matches_independent_oracle`、拒绝向量 | S2 的 J3 多根分别聚合再按共同完整坐标配对；SQL 仍由 Ibis 编译。 |
| 固定 Artifact→pandas 及当前行数值 | [`local_stage.py`](../../../marivo/analysis/materialization/local_stage.py)、[`dsl_j1_receipt.py`](../../../marivo/analysis/materialization/dsl_j1_receipt.py) | 来源/本地对照、断源 spy | compare 和新本地方法保留确切 receipt/部件，不能重进 DuckDB 或重读源。 |
| 固定 schema、部件、发布/恢复与身份 | [`execution.py`](../../../marivo/analysis/materialization/execution.py)、[`ibis_batches.py`](../../../marivo/analysis/materialization/ibis_batches.py)、[`dsl_j1_artifact.py`](../../../marivo/analysis/materialization/dsl_j1_artifact.py)、[`dsl_j1_runtime.py`](../../../marivo/analysis/materialization/dsl_j1_runtime.py) | `test_analysis_dsl_exchange.py`、`test_analysis_dsl_j1_artifact.py`、`test_analysis_dsl_j1_runtime.py` | 新方法复用同一 Store v6/receipt/Run；S2 多前驱或双 Artifact 需要先补明确输入绑定与共享图语义。 |

当前六个注册的数值方法均为版本 1；来源只准入 DuckDB，固定输入只准入 pandas，
数值首轮为 int64/float64。交换层的 Decimal 往返不赋予统计方法 Decimal 资格。
可观察的重复决策风险是来源与本地适配各自检查 Cell、键、有限值和溢出：方法政策应继续
由 registry 单一持有，适配只实现物理检查。新增后端需逐项接表/类型/时间映射、Ibis
编译与真实读批次、资源关闭、方法 route 注册、独立向量及完整发布/冷恢复资格；能力桩
不能计作真实后端通过。

## 5. 交接顺序

1. **先补 S1 共享证据**：设计一个已接受、一次执行内同一显式节点进入两个消费者的
   图，记录节点实现次数、来源读取与两个输出绑定；检查构造期零 I/O、失败关闭与
   两次顶层调用各自新求值。现有 J1 单前驱图无法代替。若需扩充切片，先修订 owning
   契约，再加最小实现及独立反例。
2. **S2 compare 与 J2**：完整同键/同域/同成员实现绑定的比较、Lazy 选择后新观察；
   固定单端加现场读取在准入前拒绝。通过正常 Store/codec 发布共享成员绑定的两份
   Artifact，再测默认 compare；独立捕获即使键相同也拒绝。
3. **S2 J3 多根组件**：订单数与明细金额分开聚合、完整坐标元组并集、合法空贡献、
   组件 ratio 和原状态上卷；区分总体 AOV 与当前行均值，记录相对本节的真实新增 owner。
4. **S3/S4 保留边界**：J4 Spearman 数值核、成本/分批与完整矩阵属于 S3；公开
   Help/typing、真实 Agent 和非 DuckDB 后端资格分别验收，不沿用本次私有 J1 结论。

W5 未修改 packaged skills、`AGENTS.md`、公开 DSL 或旧 Dataset v1 执行协议。

## 6. S2 P1 多前驱绑定与共享增量验收

**状态：P1 私有子集通过；S2 总验收未验证。** 本节是相对以上 S1 记录的增量，不改写
S1 当时 V10 的“未验证”结论。P1 开始时的 `panda` 基线为
`5766bdc7580132e48f9f5b8965480ed63c389f00`；中间的
`5794ee394619100349cd8acc413eac948eeb8de7` 独立提交只停止跟踪 S1 计划文件。
P1 代码提交为 `d89d34b264b0b94cc9f039e1c58b1956d8cb475a`。以中间提交为基线的
`git diff 5794ee394619100349cd8acc413eac948eeb8de7 HEAD -- marivo/analysis tests/test_analysis_dsl_contracts.py tests/typing/analysis_dsl_internal_contract.py`
的 SHA-256 为 `62b7a78f0a29e6a7545a4d16adbe320be77b2b8d9c3e40af83a0f7e87b8502fc`；新增
[`test_analysis_dsl_s2_p1.py`](../../../tests/test_analysis_dsl_s2_p1.py) 的文件 SHA-256 为
`de97afc55503332176ea7dcf329590165ebaa4d859c6fddf266c025042b5db0f`。
当前已跟踪工作区干净；本验收记录及 S2 计划位于 Git 忽略的本地目录。

依赖约束 `pyproject.toml`、F2 的 `tests/conftest.py`、`tests/shared_fixtures.py`、
`tests/test_analysis_dsl_fixtures.py` 的 SHA-256 均与 §1 相同。实测 Python 3.12.13、
Ibis 12.0.0、pandas 2.3.3、PyArrow 25.0.1、DuckDB 1.5.3、pytest 9.0.3。
非敏感配置沿用 §1 的本地 DuckDB、UTC、`tmp_path` 隔离和默认 xdist；没有远端服务。
P1 测试在 F2 的 J1 数据上只增加七月 B/C/D 与八月 D 的有效订单，使两期四名成员
都为 Defined。该扩展在用例自身执行，不改共享 fixture 或独立 oracle。
P1 选择最小私有 compare 作为获准双前驱图，因此实现了有界的差值算术、方法注册与
端点交换；这不是 P2 的 J2 全链验收，未实现 Difference 惰性选人、重新观察或 J2
独立 oracle。构造层固定 compare 的端点角色与检查标识，方法契约声明必要部件与
检查义务，发布层读取方法契约；`input_kinds` 按位置表示输入，允许重复 kind。

| 前驱 | 域、量/方法版本与输入绑定 | 必要部件和检查位置 |
| --- | --- | --- |
| current | 同一个 Entity 成员根上的 `observe_sum@1`，当前时间窗；来源图用该显式根，固定图用按 current 槽位选出的确切 Artifact 引用。 | 观察结果的 sum/non-null-count/row-count 部件与 receipt 随 Artifact 验证；构造期核对域/量/共同根，来源放置与方法资格在成员读取前核对，数据依赖的键/Cell 检查在发布前完成。 |
| baseline | 同一成员根、同一量/方法版本，仅时间窗不同；固定图用 baseline 槽位的确切 Artifact 引用。 | 同样保留独立 receipt；固定 Artifact 的定义指纹、共同成员实现绑定在 Run 前核对，失配拒绝不读行；固定未命中时完整 receipt/行检查在 pandas 计算与发布前完成。比较根的端点部件与 `complete_pairing`/`strict_numeric_cell` 检查由 `compare_difference@1` 契约要求。 |

| P1 子项 / 状态 | 实测结果与证据 | 边界 |
| --- | --- | --- |
| 构造、域、方法（V01/V02/V05/V13 子项）：通过 | [`test_p1_compare_constructs_shared_graph_and_rejects_independent_members`](../../../tests/test_analysis_dsl_s2_p1.py) 验证两个有序观察端点引用同一显式成员根，另建同形成员根拒绝；私有 `compare` 注册 exact pairing、Defined 数值和端点部件。 | 仅 Entity 同 Metric、同非时间事实的绝对差；J2 惰性选择/新观察、相对变化和 group compare 未验证。 |
| 来源数值、共享和再次求值（V02/V03/V10/V15 子项）：通过 | [`test_p1_source_compare_exact_keys_and_values`](../../../tests/test_analysis_dsl_s2_p1.py) 经 Ibis/DuckDB 得 A=373、B=140、C=380、D=-10；[`test_p1_shared_member_is_read_once_in_each_source_evaluation`](../../../tests/test_analysis_dsl_s2_p1.py) 对同一合法双消费者图计数：一次顶层调用一份成员读取，两次调用共两次 source open、两次成员读取，definition 相同但 Artifact 不同。 | 这是 S2 首次补出的运行内共享证据，不倒算为 S1 通过；未证明跨独立 source 查询的单事务快照。 |
| 两端固定输入、交换与命中（V06/V07/V08 子项）：通过 | [`test_p1_fixed_pair_uses_exact_shared_member_binding`](../../../tests/test_analysis_dsl_s2_p1.py) 从一次成员实现用正常 Store/codec 发布两份独立 receipt 的端点 Artifact，断源后 pandas 得同一差值；原样再调用命中原 Artifact 且不新增 Run，对调两份引用在 Run 前拒绝。 | 两份端点发布是私有契约夹具，不宣称公开双端捕获 API 或新进程 J2 冷恢复。 |
| 混合/独立捕获与失败（V08/V10/V11 子项）：通过 | [`test_p1_mixed_and_independent_fixed_inputs_reject_before_rows`](../../../tests/test_analysis_dsl_s2_p1.py) 验证 live+fixed 在 source open/Run 前拒绝、独立捕获即使键相同也在 Artifact 行读取/Run 前拒绝；[`test_p1_source_failure_closes_source_and_publishes_no_result`](../../../tests/test_analysis_dsl_s2_p1.py) 验证非 Defined 端点的失败 Run、关闭和无成功输出；[`test_p1_compare_publication_requires_registered_checks`](../../../tests/test_analysis_dsl_s2_p1.py) 验证缺方法契约要求的检查不得发布；缺一侧键由本地反例拒绝。 | 尚未测完整 S2 J2/J3 传播和多后端资源资格。 |

复现命令与实际结果：

- `make test TESTS='tests/test_analysis_dsl_contracts.py tests/test_analysis_dsl_exchange.py tests/test_analysis_dsl_execution_identity.py tests/test_analysis_dsl_j1_construction.py tests/test_analysis_dsl_j1_source.py tests/test_analysis_dsl_j1_artifact.py tests/test_analysis_dsl_s2_p1.py'`：66 passed。
- `make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py tests/test_analysis_dsl_j1_artifact.py'`：7 passed。
- `make typecheck TYPECHECK_TARGETS='marivo/analysis tests/typing/analysis_dsl_internal_contract.py'`：228 source files，无问题；`make lint-agent LINT_TARGETS='marivo/analysis tests/test_analysis_dsl_contracts.py tests/test_analysis_dsl_s2_p1.py tests/typing/analysis_dsl_internal_contract.py'`：230 files already formatted，Ruff/import contracts 通过。
- `make check-agent`：846 files already formatted，370 source files 类型检查通过，5109 passed / 4 skipped，API docs built；`git diff --check` 通过。

相对 §4 的 S1 owner，P1 在 [`dsl_j1.py`](../../../marivo/analysis/observation/dsl_j1.py)
和 Dataset descriptors/handles 新增有类型双前驱与显式绑定；方法政策由
[`dsl_j1_contracts.py`](../../../marivo/analysis/operators/dsl_j1_contracts.py) 的单一
注册管理，来源与本地仅做各自的物理检查。Ibis DAG 降低/成员复用位于
[`dsl_j1_source.py`](../../../marivo/analysis/compiler/dsl_j1_source.py) 与
[`source_stage.py`](../../../marivo/analysis/materialization/source_stage.py)，pandas
实现位于 [`local_stage.py`](../../../marivo/analysis/materialization/local_stage.py)。
[`dsl_j1_runtime.py`](../../../marivo/analysis/materialization/dsl_j1_runtime.py) 按顺序
绑定两个 Artifact；现有 Store、Run、receipt 和失败协调保持 owner，J1 exchange
用 v2 增加成员实现绑定，旧 S1 v1 交换仍可读取。来源/本地各自检查精确键与有限
Defined 值是物理路线的重复证明，不是第二份方法资格规则。公开构造、Help、site、
packaged skills 与旧 Dataset v1 路线未改。P2 的 J2 完整链、P3 的 J3、多场景成本
和 S4 公开/Agent 验收仍为**未验证**。

## 7. S2 P2 严格选人与新观察增量验收

**状态：P2 私有 J2 子集通过；S2 总验收未验证。** 本节在 §6 的 P1 之上追加，
不改写 §1–§6 各自快照时的结论。P2 的代码基线 `HEAD` 为
`d89d34b264b0b94cc9f039e1c58b1956d8cb475a`；P2 尚未提交，
`git diff HEAD -- marivo/analysis docs/specs/analysis tests/shared_fixtures.py tests/test_analysis_dsl_fixtures.py`
的 SHA-256 为 `461253da88db524898a71b38b582333e583b729e5338f7ceb88591c9b22fafe9`，
新增 [`test_analysis_dsl_s2_p2.py`](../../../tests/test_analysis_dsl_s2_p2.py) 的文件
SHA-256 为 `719b373ba3078a3c2eb02176aa7b7503992fbebbde576b3b00ceae8b41fa454f`。
本记录与 S2 实施计划仍位于 Git 忽略的本地目录，不计入上述代码差异。

依赖约束 `pyproject.toml` 与 `tests/conftest.py` 的 SHA-256 分别仍为
`b2cd14ca3775f88d30de04aaa19cde8832ee3f8a7be191286eaf1bcd800e30a9`、
`389847e76ad77f13d496cbb04b18893559036292148bb7da3cc6c6be5a226b9f`。
J2 夹具 `tests/shared_fixtures.py` 的 SHA-256 为
`e4ca858da8681cda5ff417ec2d9e10a6dc64da6bbb9299a7b1b56348702234df`；
独立 SQL oracle `tests/test_analysis_dsl_fixtures.py` 为
`f53b95fdaa4ce3fdf4e539e77d07b94208e8e38117e102f5aea6d08b0fadd594`。
这版夹具为 D 七月、C/D 八月及 C 九月分别加入一笔显式零金额订单。
四位客户的两期比较端点因此均为 Defined；C 九月零值仍占一名客户行。真正空贡献的
Revenue 仍按原政策为 Null。本次沿用 §1 的本地 DuckDB、UTC、隔离项目与默认
xdist；未接远端服务、MinIO 或其他来源后端。

| P2 子项 / 状态 | 实测结果与证据 | 边界 |
| --- | --- | --- |
| 严格 compare、域与 Cell（V01/V02/V03/V05 子项）：通过 | 独立 J2 SQL oracle 核对 A/B/C/D 的八月减七月为 `-40/20/-50/0`、下降成员 `{A,C}`、九月客户行均值 `15`；[`test_p2_j2_lazy_source_chain_matches_independent_oracle`](../../../tests/test_analysis_dsl_s2_p2.py) 核对实际 Ibis/DuckDB 链的同一数值、选中子域、Defined Cell、选中 Difference 的当前行 mean 与不提供原 Metric `rollup()`。五种谓词的来源/本地结果一致；量/单位失配、缺 D、非 Defined/非有限端点、有限且无损阈值与同形独立成员节点分别有反例。 | 只准入同一 Metric、同一直接成员节点、不同时间角色的绝对差；相对变化、跨域及多条件谓词未验证。 |
| 同次来源共享与新观察（V10/V15 子项）：通过 | [`test_p2_nested_compare_realizes_members_once`](../../../tests/test_analysis_dsl_s2_p2.py) 在 `where → members → observe → summarize(mean)` 顶层图中计数：每次来源求值一份成员读取，连续两次调用共两次 source open、两次成员读取、两个不同 Artifact；C 的九月零值参与两客户均值。空选集的九月 mean 为 `Undefined(empty_mean)`。 | 只证明此 DuckDB/Ibis 合法图中的共享；没有独立来源查询的单事务快照保证。 |
| 固定 compare、选择、部件与冷恢复（V06/V07/V08 子项）：通过 | [`test_p2_fixed_compare_select_members_and_current_mean_offline`](../../../tests/test_analysis_dsl_s2_p2.py) 用一次成员实现绑定的两份正常 Store/codec 端点 Artifact，禁止 DuckDB 连接后在 pandas 比较、选人、投影与当前行 sum/count/mean；筛选结果的两端部件按精确成员键保留，损坏一个部件使冷读取拒绝，原 compare Artifact 仍可读取。Runtime 子进程用新进程断源恢复 compare 与筛选。 | 固定双端捕获是私有测试夹具，未提供公开捕获 API；固定已选成员后现场 read/observe 仍拒绝。 |
| 准入、失败与资源（V08/V11/V13 子项）：通过 | 固定端点加现场来源、固定成员加现场 observe、独立捕获同键端点在 Run 或 Artifact 行读取前拒绝；不相容阈值在来源行读取前拒绝。删除显式零订单后，非 Defined 端点导致失败 Run、来源关闭且无成功 Artifact。 | 未验证其他后端的资源关闭或能力资格。 |

复现命令与实际结果（仓库根目录）：

- `make test TESTS='tests/test_analysis_dsl_contracts.py tests/test_analysis_dsl_exchange.py tests/test_analysis_dsl_execution_identity.py tests/test_analysis_dsl_j1_construction.py tests/test_analysis_dsl_j1_source.py tests/test_analysis_dsl_j1_artifact.py tests/test_analysis_dsl_s2_p1.py tests/test_analysis_dsl_s2_p2.py tests/test_analysis_dsl_fixtures.py'`：95 passed。
- `make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py tests/test_analysis_dsl_j1_artifact.py tests/test_analysis_dsl_s2_p2.py'`：8 passed，包括新进程断源恢复。
- `make typecheck TYPECHECK_TARGETS='marivo/analysis tests/typing/analysis_dsl_internal_contract.py'`：228 source files，无问题；`make lint-agent LINT_TARGETS='marivo/analysis tests/shared_fixtures.py tests/test_analysis_dsl_fixtures.py tests/test_analysis_dsl_s2_p2.py tests/typing/analysis_dsl_internal_contract.py'`：231 files already formatted，Ruff/import contracts 通过；`git diff --check` 通过。

相对 P1，构造与同域判定增加在 [`dsl_j1.py`](../../../marivo/analysis/observation/dsl_j1.py)
及 Dataset descriptors；方法注册在
[`dsl_j1_contracts.py`](../../../marivo/analysis/operators/dsl_j1_contracts.py)，
Ibis 降低、完整预检与运行内共享在
[`dsl_j1_source.py`](../../../marivo/analysis/compiler/dsl_j1_source.py) 和
[`source_stage.py`](../../../marivo/analysis/materialization/source_stage.py)，
pandas 当前行统计与部件筛选在
[`local_stage.py`](../../../marivo/analysis/materialization/local_stage.py)。
原 Store、Run、receipt owner 未替换；选中 Difference 的 codec 校验扩展了
[`private_parquet.py`](../../../marivo/analysis/materialization/private_parquet.py)
与 [`retained.py`](../../../marivo/analysis/materialization/retained.py)。
来源和本地各自执行 Cell/键的物理检查，方法资格仍归单一 registry。
`make check-agent` 按 S2 实施计划留给 P4 集成门禁；J3/J4、全量 V01–V15、
公开签名/Help/site/packaged skills、真实 Agent 与非 DuckDB 后端仍为**未验证**。

## 8. P2 对抗性审查意见收敛

**状态：P2 review 修复通过；P3 由独立任务开发。** 本节记录 §7 快照之后的
P2 小范围修订。基线 `HEAD` 仍为
`d89d34b264b0b94cc9f039e1c58b1956d8cb475a`，P2 改动仍未提交。
`git diff HEAD -- marivo/analysis docs/specs/analysis tests/shared_fixtures.py tests/test_analysis_dsl_fixtures.py`
的 SHA-256 为 `9e1a9054e48979103dd8c607aea4ee17f0e171dcc8fda6814a30570505b83c0d`；
[`test_analysis_dsl_s2_p2.py`](../../../tests/test_analysis_dsl_s2_p2.py) 的文件
SHA-256 为 `3bee0169e6852995751434c460c658b24cc15dd3a0c6f8285b877fbddc2526bb`。
共享夹具和独立 SQL oracle 的文件 SHA-256 仍与 §7 相同。

| Review 意见 | 处理与证据 |
| --- | --- |
| 单次成员读取测试丢弃图 handle | 测试显式命名五个 handle，并逐边断言 compare 两端共享确切成员节点、筛选、投影和新观察的输入关系。 |
| 同范围 compare 政策 | `time_change` 要求两个不同时间范围；构造期拒绝自身比较，新增反例并更新设计规格。 |
| 已选 Difference 暴露 `value` 却不能再次 `where` | 移除已选关系的 `value`；P2 只支持单个数值谓词，规格明确其后可调用 `members()` 或当前行统计。 |
| 固定路线零行选择 | 新增断源固定比较后的空选择与 Store 冷读取断言：主表零行，两端精确键部件也各零行。 |
| 来源图递归遍历风格 | 改为与现有待处理队列一致的迭代后序遍历，保持节点去重与共享成员预实现顺序。 |
| 浮点 `eq` 语义 | 两份规格均明确 float64 `eq` 是无容差的二进制精确相等，来源与本地路线一致。 |
| `where` 的 shape/schema 判别 | 当前仅有两种封闭的私有 `where` 形态；四列数值形态及两个端点角色仍经独立部件、receipt 和精确键校验。保留该有界判别；显式 marker 可在 P4 集成时连同 codec 身份一起评估。 |

复核命令与实际结果：

- `make test TESTS='tests/test_analysis_dsl_contracts.py tests/test_analysis_dsl_exchange.py tests/test_analysis_dsl_execution_identity.py tests/test_analysis_dsl_j1_construction.py tests/test_analysis_dsl_j1_source.py tests/test_analysis_dsl_j1_artifact.py tests/test_analysis_dsl_s2_p1.py tests/test_analysis_dsl_s2_p2.py tests/test_analysis_dsl_fixtures.py'`：95 passed。
- `make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py tests/test_analysis_dsl_j1_artifact.py tests/test_analysis_dsl_s2_p2.py'`：8 passed。
- `make typecheck TYPECHECK_TARGETS='marivo/analysis tests/typing/analysis_dsl_internal_contract.py'`：228 source files，无问题。
- `make lint-agent LINT_TARGETS='marivo/analysis tests/shared_fixtures.py tests/test_analysis_dsl_fixtures.py tests/test_analysis_dsl_s2_p2.py tests/typing/analysis_dsl_internal_contract.py'`：231 files already formatted，Ruff/import contracts 通过；`git diff --check` 通过。

本节仅是 P2 私有 J2 review 的复核；`make check-agent`、P3/J3 和公开 DSL
仍按 S2 计划由后续独立阶段验收。

## 9. S2 P3 多根 ratio 与原状态上卷增量验收

**状态：P3 私有 J3 通过；P4 集成汇总未执行，公开 Analysis DSL 未激活。**
基线为 `panda` 的 `536c4a765d845070bf39df7eab9f14a99d9c2fb9`；P3 代码和文档
仍是未提交工作区改动，未覆盖 P2。依赖约束 `pyproject.toml` 与
`tests/conftest.py` 的 SHA-256 仍为
`b2cd14ca3775f88d30de04aaa19cde8832ee3f8a7be191286eaf1bcd800e30a9`、
`389847e76ad77f13d496cbb04b18893559036292148bb7da3cc6be5a226b9f`。
J3 夹具 `tests/shared_fixtures.py` 的 SHA-256 是
`85e2a510246c19785f1d2cb5508f175d584b86a2e3caa3064f1a7a629e7ee973`；
独立 SQL/算术 oracle `tests/test_analysis_dsl_fixtures.py` 仍为
`f53b95fdaa4ce3fdf4e539e77d07b94208e8e38117e102f5aea6d08b0fadd594`。
新增 Ibis 实现 [`dsl_j3_ratio.py`](../../../marivo/analysis/compiler/dsl_j3_ratio.py) 与
[`test_analysis_dsl_s2_p3.py`](../../../tests/test_analysis_dsl_s2_p3.py) 的 SHA-256 分别为
`e4ecb4d78f3b25bb78b4db6fd2f27d8deec643033b4420d9bad39fcbf38098a7`、
`2a38f7b700859b22d3f22b1a5bf9d51f87576faadde81b6a605298b597edccfb`。
全部 J3 订单为 paid；AOV 不含隐式状态过滤。

| P3 子项 / 状态 | 实测证据 | 边界 |
| --- | --- | --- |
| 正式声明与构造：通过 | Semantic builder 接受显式 `time_via`、`nulls`、`empty`、`count(time=)`、`zero_denominator`；J3 夹具声明 LineOrder 时间路径、零空贡献与 Undefined 零分母。不连续时间路径及错误组件路线在加载或构造期拒绝；Semantic Help 和英中 site 示例对齐。 | 仅新增声明，Analysis 公开入口仍未激活；P3 方法只准入显式 Undefined 政策的直接 sum/count ratio。 |
| 来源组件域和值（V01–V05 子项）：通过 | 独立 SQL/算术 oracle 与实际 Ibis/DuckDB 结果一致：A/web=`100`、A/mobile=`0`、B/web=`30`，web=`160/3`、mobile=`0`，总体 `40`，当前行均值 `130/3`；权重反例中客户均值 `50.5`、原组件总体 `200/101`。A/web 两条明细只计一笔订单；两个坐标仅产生实际完整元组，不构造笛卡尔积；仅分母坐标补零，零分母保留 Undefined。 | 来源仅 DuckDB/Ibis；不将订单与明细先 join 后计数。 |
| 覆盖与状态拒绝（V08/V11 子项）：通过 | 缺路径覆盖、重复源身份、错误映射和不连续时间路径均拒绝；重命名 domain、Entity、字段、关系及 Metric 后同样得到总体 `40`。 | 来源完整性检查采取保守拒绝；其他后端未验证。 |
| Artifact 与断源 pandas（V06–V08 子项）：通过 | 主结果及五份精确键保留部件经同一交换和 receipt 发布，方法版本为 1；固定 Artifact 的总体/渠道 rollup 与来源结果一致。禁用 `duckdb.connect` 后固定续算仍通过；损坏一份部件后冷读取拒绝。 | 已验证同进程断源固定续算及 Store 重新打开后的损坏拒绝；未声明跨进程冷恢复证据。 |

复现命令与实际结果（仓库根目录）：

- `make test TESTS='tests/test_analysis_dsl_s2_p3.py tests/test_analysis_dsl_fixtures.py::test_j3_independent_component_aggregation_and_two_distinct_means tests/test_semantic_live_registry.py::test_validate_semantic_live_surface_passes tests/test_cutover_documentation_examples.py::test_bilingual_examples_have_identical_executable_contracts'`：14 passed，1 个 Runtime 用例被默认筛除。
- `make runtime-test TESTS='tests/test_analysis_dsl_s2_p3.py tests/test_analysis_dsl_j1_runtime.py tests/test_analysis_dsl_j1_artifact.py'`：8 passed。
- `make check-agent`：lint/import contracts、371 个源文件 typecheck、5132 passed/4 skipped 默认测试、API 文档构建均通过；`git diff --check` 通过。

相对 P2，新增的语义 owner 在 Semantic IR、validator、metric graph lowering 与 CatalogDetails；
私有构造与方法注册在 `dsl_j1.py`、`dsl_j1_contracts.py`，来源 Ibis 降低在
`dsl_j3_ratio.py`，pandas 原状态续算在 `local_stage.py`，Artifact/receipt 仍用 J1
现有 Store/Run/交换链并增加 ratio 部件白名单与完整元组键。P4 仍须执行 S2 汇总验收，
本节不将私有 P3 结果计为公开 DSL、真实 Agent、其他后端或 S3/S4 验收。

## 10. S2 P4 集成回归、增量成本与阶段验收

**状态：S2 私有 J2/J3 适用子项通过；完整 MVP 验证矩阵未关闭。** 本节使用
`panda` 的 `eda6e01bfc6cbcc7e5a90c5047a99cea1f65e8d2` 作为代码基线，
在其上仅修改 `tests/test_analysis_dsl_s2_p1.py`、`tests/test_analysis_dsl_s2_p3.py`
并更新本地验收文档。测试文件最终 SHA-256 分别为
`3271a856245a7dd70a5d909a61e3c7d6a4542ee8dda50197f5f1c27302625786`、
`a76f805ebc4830a58d8957d1d7690303f617838f6bdf05f76cfd23f7169c0067`。
实施前跟踪工作区干净；本记录及 S2 计划仍在 Git 忽略目录。§1–§9 的历史
快照和当时的状态不据本节倒改。

依赖约束 `pyproject.toml` 的 SHA-256 为
`b2cd14ca3775f88d30de04aaa19cde8832ee3f8a7be191286eaf1bcd800e30a9`；
fixture 版本由 `tests/conftest.py`、`tests/shared_fixtures.py`、独立 SQL oracle
`tests/test_analysis_dsl_fixtures.py` 的 SHA-256 固定，依次为
`389847e76ad77f13d496cbb04b18893559036292148bb7da3cc6c6be5a226b9f`、
`85e2a510246c19785f1d2cb5508f175d584b86a2e3caa3064f1a7a629e7ee973`、
`f53b95fdaa4ce3fdf4e539e77d07b94208e8e38117e102f5aea6d08b0fadd594`。
本地 Python 3.12.13、Ibis 12.0.0、pandas 2.3.3、PyArrow 25.0.1、
DuckDB 1.5.3、pytest 9.0.3、mypy 2.3.0；非敏感配置为本地 DuckDB、UTC、
`tmp_path` 隔离项目/Session/Store、默认 xdist 和两个 Runtime worker。
冷恢复子进程用 `MARIVO_PROJECT_ROOT` 指向该用例的隔离项目，禁止 DuckDB 连接。
没有启动远端服务或 MinIO。

| 复现命令（仓库根目录） | 实际结果 |
| --- | --- |
| `make test TESTS='tests/test_analysis_dsl_fixtures.py tests/test_analysis_dsl_contracts.py tests/test_analysis_dsl_exchange.py tests/test_analysis_dsl_execution_identity.py tests/test_analysis_dsl_j1_construction.py tests/test_analysis_dsl_j1_source.py tests/test_analysis_dsl_j1_artifact.py tests/test_analysis_dsl_s2_p1.py tests/test_analysis_dsl_s2_p2.py tests/test_analysis_dsl_s2_p3.py tests/test_semantic_live_registry.py tests/test_cutover_documentation_examples.py'` | **通过**：150 passed；Runtime 标记由默认门禁排除。 |
| `make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py tests/test_analysis_dsl_j1_artifact.py tests/test_analysis_dsl_s2_p1.py tests/test_analysis_dsl_s2_p2.py tests/test_analysis_dsl_s2_p3.py'` | **通过**：11 passed，包括 J1 旧 Artifact、J2 端点冷恢复及两个 P4 新用例。 |
| `make typecheck TYPECHECK_TARGETS='marivo/semantic marivo/analysis tests/typing/analysis_dsl_internal_contract.py'` | **通过**：280 source files，无问题。 |
| `make lint-agent LINT_TARGETS='marivo/semantic marivo/analysis tests/test_analysis_dsl_s2_p1.py tests/test_analysis_dsl_s2_p3.py'` | 首次 **失败**：P3 新测试的格式与导入排序；对该文件运行 Ruff format 和 `ruff check --fix` 后，完整 `make check-agent` 的 lint/import 检查**通过**。 |
| `make check-agent`；`git diff --check` | **通过**：849 files already formatted，Ruff/import contracts 通过，371 source files 类型检查通过，5132 passed / 4 skipped，API docs built；差异无空白错误。 |

| S2 适用验证子项 | 状态与证据 | 未覆盖边界 |
| --- | --- | --- |
| V01–V03：J2/J3 独立预期、完整域、Cell 与原状态 | **通过**：§7 的 J2 数值 `-40/20/-50/0`、选人 `{A,C}`、九月 mean `15`；§9 的 J3 坐标、`160/3` 渠道值、总体 `40`、当前行 mean `130/3` 及加权反例，由独立 SQL/算术 oracle 对照。 | J4、全部目标方法与全量 V01–V03 未验证。 |
| V04–V05：严格拒绝与已准入双路线 | **通过**：缺键、错误量/单位、非 Defined、缺覆盖、零分母及来源/本地 J1/J2/J3 适用结果由上述定向测试复核；不以 reducer 或 Ibis 编译 SQL 生成期望。 | 全类型、全后端与完整拒绝矩阵未验证。 |
| V06–V07：交换、部件与断源 K | **通过**：J1/J2 receipt 与冷恢复回归；新 [`test_p3_ratio_recovers_original_components_in_fresh_process`](../../../tests/test_analysis_dsl_s2_p3.py) 在新进程重建同一声明与图，禁止 DuckDB 连接，用保存的 J3 组件得到总体 `40`、web=`160/3`、mobile=`0`。 | 其他后端、J4 与全量 K 未验证。 |
| V08：来源身份、固定命中与混合准入 | **通过**：J1 重复来源求值、固定精确命中、旧 Artifact 恢复；P1/P2 双端共同绑定通过，独立捕获同键和 live+fixed 在读前拒绝。 | 不宣称公开双端捕获 API。 |
| V10：共享、发布原子性与关闭 | **通过**：P1/P2 的合法多消费者图在一次运行内实现一份显式成员节点；新 [`test_p1_multi_predecessor_publication_failure_preserves_prior_artifact`](../../../tests/test_analysis_dsl_s2_p1.py) 在 compare 提交前注入故障，失败 Run 无成功 Artifact，先前双前驱 Artifact 可从重新打开的 Store 精确恢复；来源失败关闭由 P1/J1 用例复核。 | 不宣称跨独立查询的单事务快照或其他后端资源资格。 |
| V11：路线准入 | **通过**：已接入 DuckDB/Ibis 来源与 pandas 固定路线的混合早拒绝、缺路径覆盖及不连续时间路径拒绝按 P1–P3 用例复核。 | 后端能力桩、第二真实来源后端及完整失败后不兜底矩阵未验证。 |
| V13：扩展代价 | **通过**：下段记录实际 owner、文件差异和非电商重命名反例；未建立第二套 AST、Store 或方法 registry。 | S3 的规模/分批运行成本未验证。 |
| V15：SQL 委托的已准入路线 | **通过**：J2/J3 来源表达式交 Ibis 编译，在真实 DuckDB 上对独立 SQL oracle 验证，固定输入由 pandas 续算。 | 后端不支持而已注册 Python 路线及完整三路线矩阵未验证。 |

相对 S1 §4 的基线 `5766bdc758`，S2 在语义声明/验证与 metric graph lowering、
私有 Logical 构造/方法契约、Ibis 来源降低、pandas 当前行与组件原状态续算、
原 Store/Run/receipt 的多前驱绑定和 codec 校验、P1–P3 测试处有实际改动。
`git diff --stat 5766bdc758..eda6e01bfc6c -- marivo/analysis marivo/semantic tests docs/specs site/src/content/docs`
显示这些范围共 42 个已提交文件、3929 行新增及 211 行删除；P4 另外只补两个测试文件。
正式 Metric/时间和值政策归 Semantic，方法资格与必要检查归单一私有 registry；
Ibis 与 pandas 分别实现相同的物理 Cell/ratio 运算，需要上述双路线反例持续防漂移，
未发现需要另建语义 owner 的重复政策。新增的普适能力是显式多前驱绑定、同次图共享、
组件状态的保存与原状态归约；J1 回归由本节 150 个定向默认测试、11 个 Runtime 测试
和完整默认门禁共同覆盖。

失败复现入口：对多前驱提交故障运行
`make runtime-test TESTS='tests/test_analysis_dsl_s2_p1.py::test_p1_multi_predecessor_publication_failure_preserves_prior_artifact'`；
对 J3 断源新进程恢复运行
`make runtime-test TESTS='tests/test_analysis_dsl_s2_p3.py::test_p3_ratio_recovers_original_components_in_fresh_process'`；
对 J1 旧 Artifact 与协调运行 §2 所列的定向 Runtime 节点。S2 私有出口已满足；
S1 历史总验收、J4、完整 V01–V15、公开签名/Help/site、真实 Agent 和其他后端仍为
**未验证**。

## 11. S3 P1 J4 来源准备与 Python Spearman 增量验收

**状态：P1 私有适用子项通过；P2–P4 和 S3 总验收未关闭。** 本节相对 §10
增量记录，不倒改历史结论。实施起点为 `panda` HEAD
`37895dec290a9e89ebdb0aa354a4c5441cbe8a0c`，跟踪工作区干净；当前改动
尚未提交。`pyproject.toml`、`tests/conftest.py`、`tests/shared_fixtures.py`、
`tests/test_analysis_dsl_fixtures.py` 的 SHA-256 依次为
`b2cd14ca3775f88d30de04aaa19cde8832ee3f8a7be191286eaf1bcd800e30a9`、
`389847e76ad77f13d496cbb04b18893559036292148bb7da3cc6c6be5a226b9f`、
`85e2a510246c19785f1d2cb5508f175d584b86a2e3caa3064f1a7a629e7ee973`、
`f53b95fdaa4ce3fdf4e539e77d07b94208e8e38117e102f5aea6d08b0fadd594`。
新增测试 `tests/test_analysis_dsl_s3_p1.py` 的 SHA-256 为
`6879ada178f64019ebbbf6e1c36e67dddeaea3b87e21bfc6ad292a751cfb9670`。
依赖版本、隔离配置和本地 DuckDB 环境沿用 §10；本节未启动远端服务或 MinIO。

| 层次 / 状态 | 实测证据 | 边界 |
| --- | --- | --- |
| 构造与绑定：**通过** | 两份真实 Metric 从同一个显式成员节点构造有序左右端点；构造不打开业务来源；独立成员根在构造时拒绝。两端必须同 Session、Entity、时间范围且无坐标。 | 私有 `J4Association`，公开 API 未激活。 |
| Ibis 编译及真实 DuckDB 来源准备：**通过** | [`test_j4_spearman_uses_real_source_and_python_kernel`](../../../tests/test_analysis_dsl_s3_p1.py) 对 `j4` 和 `j4_ties` 运行真实 DuckDB/Ibis；来源成员节点仅实现一次，两个观察端点各读取一次。来源前置检查编译 Ibis 表达式验证完整、唯一键域；缺键反例拒绝并关闭 reader。 | 来源端限当前 DuckDB 资格，不宣称第二后端或跨独立查询的单事务快照。 |
| Python Association 数值核：**通过** | 原始 J4 四对得到 Spearman `-0.4`，独立并列值夹具得到 `7/9`；计数为 input/matched/complete `4/4/4`，状态 `valid`，保留左右 Metric 身份与方法版本。乱序按键配对；无订单成员保留 count=0，Revenue Null 后得到 matched/null/complete `4/1/3`、系数 `-0.5`。缺键、重复键、Unknown Cell、非有限、Decimal、常量及不足完整对分别拒绝。 | 不将系数当作可上卷的充分状态；未产生可发布 Artifact。 |
| pandas、交换与冷恢复：**未验证** | P1 只运行来源准备和 Python 数值核。 | P2 负责发布、固定双输入、pandas 续算与断源恢复。 |
| 分批、能力桩与成本：**未验证** | 本节仅观察来源 reader 关闭和共享次数。 | P3 负责跨批秩与内存、三路线能力桩及规模成本。 |

复现命令与结果：

| 命令（仓库根目录） | 实际结果 |
| --- | --- |
| `make test TESTS='tests/test_analysis_dsl_s3_p1.py tests/test_analysis_dsl_j1_construction.py tests/test_analysis_dsl_j1_source.py tests/test_lazy_correlation_numeric.py'` | **通过**：67 passed，其中 P1 新测试 15 个。 |
| `make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py tests/test_analysis_dsl_s2_p1.py'` | **通过**：7 passed，复核既有 Runtime 边界；未覆盖 P2 的 J4 发布或冷恢复。 |
| `make lint-agent LINT_TARGETS='marivo/analysis/compiler/dsl_j1_source.py marivo/analysis/compiler/placement.py marivo/analysis/materialization/dsl_j1_runtime.py marivo/analysis/materialization/dsl_j4_source.py marivo/analysis/observation/dsl_j1.py marivo/analysis/operators/association_values.py marivo/analysis/operators/dsl_j1_contracts.py marivo/analysis/operators/dsl_j1_values.py marivo/analysis/operators/registry.py tests/test_analysis_dsl_s3_p1.py'` | **通过**：10 个文件格式、Ruff 和导入契约检查。 |
| `make check-agent`；`git diff --check` | **通过**：851 个文件格式检查、Ruff/导入契约、372 个源文件类型检查、5147 passed / 4 skipped、API docs built；差异无空白错误。 |

失败复现入口在新增测试中：`test_j4_ibis_pair_preflight_rejects_missing_key_and_closes_reader`
覆盖来源键域与资源边界；`test_j4_rejects_invalid_or_unusable_pairs` 及
`test_j4_rejects_insufficient_complete_pairs` 覆盖数值拒绝。方法政策仍归现有
Association owner；新增的来源放置、count 观察、Ibis 键域资格与私有构造分别在
`dsl_j4_source.py`、`dsl_j1_source.py`、`dsl_j1.py` 和方法 registry/contract 接入，
未另建 AST、Store 或相关算法政策。P1 结果不提升 P2–P4、完整 V01–V15、
公开签名/Help/site、真实 Agent 或其他真实后端资格。

## 12. S3 P2 Association 发布、固定续算与冷恢复增量验收

**状态：P2 私有子项通过；P3–P4 与 S3 总验收未关闭。** 本节以 P1 提交
`ca904e72da` 为代码基线，验收时的 P2 改动仍在工作区。运行环境为 Ibis
12.0.0、pandas 2.3.3、PyArrow 25.0.1、DuckDB 1.5.3、SciPy 1.17.1、
NumPy 2.4.6；使用 `tests/shared_fixtures.py` 的 J4 真实声明和本地 DuckDB
事实夹具。没有启动远端服务。

| P2 子项 | 结果与证据 | 边界 |
| --- | --- | --- |
| J4 来源发布 | **通过**：`test_j4_source_publishes_and_filters_coefficient` 验证 `-0.4`、完整对数 `4`、有序 Metric 键及重复来源求值产生不同 Run/Artifact。主表、`pair_counts`、方法版本、检查与 exact receipt 进入现有 Store。 | 私有 `execute_j1` 路由；不等于公开 DSL。 |
| pandas K | **通过**：固定 Artifact 上的 coefficient `where` 保留负系数；当前行 count=`1`、mean=`-0.4`，空筛选 sum=`0.0`、count=`0`、mean=Undefined(`empty_mean`)。筛选与统计不重新计算 Spearman。 | 系数不能恢复 Entity 成员或作为原 Metric 状态上卷。 |
| 同次固定双输入与断源冷恢复 | **通过**：`capture_j4_endpoints` 从同一成员实现生成两份完整观察，通过正常 codec/Store 发布。新进程禁止 DuckDB connect，精确读取来源结果并执行纯固定 Spearman 与筛选，仍为 `-0.4` / 完整对数 `4`。固定命中保持原 Run/Artifact。 | 捕获仅是受控私有夹具；无公开双端捕获入口。 |
| 拒绝与原子性 | **通过**：来源加固定输入、同键独立捕获、错方法版本均在相应边界拒绝；`pair_counts` receipt 损坏阻止恢复。提交前故障留下失败 Run、无成功 Artifact，旧 Artifact 仍可精确恢复。 | 未把未知提交结果当作允许自动重放来源。 |

验证命令与结果：

- `make test TESTS='tests/test_analysis_dsl_s3_p1.py tests/test_analysis_dsl_s3_p2.py tests/test_analysis_dsl_s2_p1.py tests/test_analysis_dsl_s2_p2.py tests/test_analysis_dsl_s2_p3.py'`：47 passed，覆盖 S2 和 J4 定向默认测试。
- `make runtime-test TESTS='tests/test_analysis_dsl_s3_p2.py'`：7 passed，包含新进程断源、错版本/绑定、损坏 receipt 和原子发布故障。
- 触及模块 `make typecheck TYPECHECK_TARGETS='...'` 与 `make lint-agent LINT_TARGETS='...'`：通过。
- 最终 `make check-agent`：852 个文件格式检查、Ruff/导入契约、372 个源文件类型检查、5148 passed / 4 skipped，API docs built。

P3 的跨批完整秩、路线能力桩和成本实测，P4 的完整 V01–V13/V15
矩阵与扩展代价仍未验证；本节只确认 P2 的私有发布及 K。

## 13. S3 P3 分批、来源数值路线与成本增量验收

**状态：P3 私有子项通过；P4 与 S3 总验收未关闭。** 实施基线为
`6332d8b5534f946c71332ca90613766709fb8c63`，P3 改动仍在工作区。
按用户确认修订了 S3 原有“Spearman 数值只在 Python”的边界；本次新增的
DuckDB/Ibis 来源数值路线限同 Entity、无 lag、int64/float64 J4。
环境为 macOS、Python 3.12.13、Ibis 12.0.0、pandas 2.3.3、PyArrow
25.0.1、DuckDB 1.5.3、SciPy 1.17.1、NumPy 2.4.6。

| P3 子项 | 结果与证据 |
| --- | --- |
| 来源数值及政策 | **通过**：`test_analysis_dsl_s3_p3.py` 对原始 `-0.4`、并列值 `7/9` 和 Null 后 `-0.5` 比对 Python 路线的身份、状态、计数及系数；无完整候选拒绝。来源端通过 Ibis 平均秩和相关聚合产生同一受控 J4 结果，沿用 P2 发布及冷恢复路径。 |
| 分批与流 | **通过**：1/2/3/32 行批次切分不改并列值排名和计数；现有 `test_analysis_dsl_exchange.py` 覆盖零批次保留 schema、跨批漂移、提前 close、迭代失败及完成标记，P3 另测合法零列 Ibis reader。业务行序反例仍由 P1 的键配对测试持有；受控交换要求已排序的键流。 |
| 放置与失败 | **通过**：独立能力桩覆盖来源数值、仅来源准备加 Python、无路线和缺必需检查的注册拒绝；实际来源执行失败不触发 Python 重试。桩不授予其他后端资格。 |

运行成本由 `PYTHONPATH=. .venv/bin/python devtools/analysis_dsl_s3_p3_cost.py --all`
在每个布局/规模的独立进程测得。三条路线使用相同 August 事实和 Entity 范围；
`fixed_pandas` 包含同次成员捕获、两份输入 Artifact 发布和本地计算，另两条包含
来源计算和结果发布。计时不含最后的结果展示读取。`MB` 下表按十进制换算，
耗时和峰值 RSS 是本机单次观测值，不作性能阈值。

| 事实 / 成员 | 布局 | 查询 / 批次 | 交换 MB | Artifact 读 / 总物化 MB | 完整端点 Arrow MB | 峰值 RSS MB | 秒 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 / 250 | 来源数值 | 3 / 3 | 0.002 | 0 / 0.003 | 未收集 | 291.8 | 0.179 |
| 1,000 / 250 | 先物化后 pandas | 9 / 9 | 0.036 | 0.004 / 0.006 | 0.028 | 290.1 | 0.313 |
| 1,000 / 250 | 来源→Python | 9 / 9 | 0.036 | 0 / 0.003 | 0.028 | 289.6 | 0.208 |
| 100,000 / 25,000 | 来源数值 | 3 / 27 | 0.242 | 0 / 0.003 | 未收集 | 339.8 | 0.294 |
| 100,000 / 25,000 | 先物化后 pandas | 9 / 81 | 3.714 | 0.103 / 0.105 | 2.862 | 359.2 | 1.443 |
| 100,000 / 25,000 | 来源→Python | 9 / 81 | 3.714 | 0 / 0.003 | 2.862 | 347.4 | 0.644 |

100,000 事实运行中的来源 reader 最大批次为 1,024 行，确实分批；Python 路线
仍须同时持有完整端点 Arrow Table 和秩/相关计算工作区。来源→Python 的峰值
RSS 在 Arrow 收集后为 342.5 MB、数值核后为 347.4 MB；先物化后 pandas
在捕获端点后为 339.8 MB、进入数值核前为 359.1 MB、核后为 359.2 MB。
两份完整端点的 Arrow payload 为 2.862 MB；固定路线的 pandas 转换至少
复制该量，实际对象/NumPy/SciPy 分配由进程 RSS 峰值包住，不能从输入批次
1,024 行推断上界。来源数值路线不收集完整端点，但仍收集 25,000 个成员；
上述内存数据是进程峰值，不声称精确分摊每个库的 native allocation。
未设置预算、抽样、截断、spill 或自动近似；远端资格与性能未验证。

验证命令：`make test TESTS='tests/test_analysis_dsl_s3_p1.py tests/test_analysis_dsl_s3_p2.py tests/test_analysis_dsl_s3_p3.py tests/test_analysis_dsl_exchange.py tests/test_analysis_dsl_contracts.py tests/test_lazy_local_placement.py'`
通过（60 tests，当时 P3 增补两个无效 Cell 测试之前）；最终
`make test TESTS='tests/test_analysis_dsl_s3_p3.py tests/test_analysis_dsl_s3_p2.py'`
通过（13 tests）。`make runtime-test TESTS='tests/test_analysis_dsl_s3_p2.py tests/test_analysis_dsl_s3_p3.py'`
通过（7 tests）；触及模块定向 typecheck、lint-agent 通过；最终
`make check-agent` 通过（5160 passed / 4 skipped、372 个源文件类型检查、
API docs built），`git diff --check` 通过。P4 仍负责完整矩阵与阶段关闭。

## 14. S3 P4 全矩阵回归、扩展代价与 S4 交接

**状态：S3 私有首轮必要项通过；S4 未开始。** 基线为 `panda` HEAD
`722a7b515dbe6ad892c590e5be601f2124fcbf4`；实施前跟踪工作区干净，
P3 已提交。本轮只新增
[`test_analysis_dsl_s3_p4.py`](../../../tests/test_analysis_dsl_s3_p4.py)，
不改数值核或公开 API。§1–§13 保留当时的阶段结论。计划和本记录所在目录
由 Git 忽略，不将其中内容算作已提交代码。

环境为 macOS、Python 3.12.13、Ibis 12.0.0、pandas 2.3.3、
PyArrow 25.0.1、DuckDB 1.5.3、SciPy 1.17.1、NumPy 2.4.6、
pytest 9.0.3。本地 DuckDB、UTC、每用例独立 `tmp_path` 项目/Session/Store；
默认测试使用 xdist，Runtime 使用两个 worker。没有启动远端服务或 MinIO。
`pyproject.toml`、`tests/conftest.py`、`tests/shared_fixtures.py`、
`tests/test_analysis_dsl_fixtures.py`、P4 新测试的 SHA-256 依次为
`b2cd14ca3775f88d30de04aaa19cde8832ee3f8a7be191286eaf1bcd800e30a9`、
`389847e76ad77f13d496cbb04b18893559036292148bb7da3cc6c6be5a226b9f`、
`85e2a510246c19785f1d2cb5508f175d584b86a2e3caa3064f1a7a629e7ee973`、
`f53b95fdaa4ce3fdf4e539e77d07b94208e8e38117e102f5aea6d08b0fadd594`、
`57b9393571716d5119d80c470e5671d558a7ca217d56f16900c541487e6f0aa1`。

以下“通过”只指 S3 **已实现切片的首轮必要项**，不等于完整公共矩阵通过。
表中的测试均在本轮定向默认或 Runtime 回归中重跑；本轮适用项
通过 14 项，失败 0 项，阻塞 0 项。各项的公共/后端未验证边界在末列登记。

| 项 | 状态与具体证据 | 边界 |
| --- | --- | --- |
| V01 组合 | **通过**：J1–J3 独立 SQL/算术 oracle 见 §2、§7、§9；[`s3_p1`](../../../tests/test_analysis_dsl_s3_p1.py)、[`s3_p2`](../../../tests/test_analysis_dsl_s3_p2.py)、[`s3_p3`](../../../tests/test_analysis_dsl_s3_p3.py) 核对 J4 `-0.4`、并列值 `7/9`、Null 后 `-0.5`、域/状态/计数和筛选。 | 私有执行。 |
| V02 完整域与多根 | **通过**：[`j1_source`](../../../tests/test_analysis_dsl_j1_source.py) 的零订单成员、[`s2_p3`](../../../tests/test_analysis_dsl_s2_p3.py) 的完整元组并集与明细/订单不同粒度。 | 限已声明路径。 |
| V03 状态与空输入 | **通过**：J1 空 sum/count/mean、J3 原状态总体比率与当前行均值、`s3_p2` 的空筛选 Undefined。 | 相关系数不作总体上卷。 |
| V04 拒绝 | **通过**：`j1_source`、[`s2_p1`](../../../tests/test_analysis_dsl_s2_p1.py)、[`s2_p2`](../../../tests/test_analysis_dsl_s2_p2.py)、`s3_p1`、`s3_p2` 复核 Null 分类、缺键/单位/覆盖、跨 Session、非有限、Decimal 数值准入、错绑定/版本等。 | 未来方法/后端未覆盖。 |
| V05 双实现 | **通过**：J1–J3 已准入来源/本地续算；`s3_p1`/`s3_p3` 用独立平均秩 oracle 和同向量核对 J4 Python 与来源数值的身份、Cell、计数、状态及拒绝。 | 不要求相同物理计划。 |
| V06 交换与 codec | **通过**：[`exchange`](../../../tests/test_analysis_dsl_exchange.py) 核对两类生产者、四 Cell、零批次 schema、nullable int64、大整数、时间戳/Decimal 精度、部件换序及漂移/损坏拒绝；`s3_p2` 核对 J4 `pair_counts` receipt。 | Decimal 往返不授予数值准入。 |
| V07 pandas 与冷恢复 | **通过**：定向 Runtime 覆盖 J1–J4 已承诺 K；`s3_p2` 在新进程断源并禁止 DuckDB 连接后恢复 J4 来源及双固定输入并继续筛选。 | 不从旧系数重算 Spearman。 |
| V08 身份、命中与混合 | **通过**：[`j1_runtime`](../../../tests/test_analysis_dsl_j1_runtime.py) 同一 Lazy 对象修改来源后二次求值、精确固定命中、失败后旧 Artifact；`s2_p1`/`s3_p2` 复核共同绑定、混合早拒绝及独立捕获拒绝。 | 双端捕获是私有夹具。 |
| V09 数值边界 | **通过**：`s3_p1`/`s3_p3` 核对平均秩、乱序、Null、计数、状态及 1/2/3/32 行批次；两路线均在真实 DuckDB 上执行。 | 其他方法和 lag 未准入。 |
| V10 惰性、共享与原子性 | **通过**：`s3_p1` 构造零业务读取、一次图内共享；`j1_runtime`/`s2_p1`/`s3_p2` 覆盖独立求值、失败/不明提交协调和旧产物保护；`exchange` 覆盖提前关闭及迭代失败。 | 只授予当前 DuckDB 资源资格，不声称跨查询事务快照。 |
| V11 放置 | **通过**：`s3_p3` 能力桩覆盖来源数值、来源准备→Python、无合法路线及执行失败不兜底；`j1_source` 覆盖未准入后端/类型早拒绝。 | 桩不是第二真实后端。 |
| V12 分批与成本 | **通过**：`s3_p3`/`exchange` 复核批次与资源；下表在当前 HEAD 重测三布局和两种规模。 | 本机单次观测，无性能阈值。 |
| V13 扩展代价 | **通过**：下文 owner 映射及差异计数；P4 新测试使用设备读数域，重命名 Entity、关系、Metric、物理表和字段，并将量单位改为 kWh，两条 J4 路线仍为 `-0.4`/4 完整对。 | 两条物理数值路径须继续对照。 |
| V15 SQL 与路线 | **通过**：`s3_p1`/`s3_p3` 在 DuckDB 上运行 Ibis 编译的准备/数值表达式；能力桩区分可编译与语义准入，运行失败不转 pandas。 | 其他真实后端未验证。 |

L1 的共同原域筛选由 J2 已实现的选择/观察反例覆盖；L6 的固定 K 由
`j1_runtime`、`s2_p3`、`s3_p2` 的断源续算覆盖；L8 的原状态与
当前行统计区分由 `s2_p3` 覆盖；L9 的含空组目标域由 `s2_p3` 空组
用例覆盖。这些是固定前提下的实例检查，**通用代数性质未验证**。

| 复现命令 | 本轮实际结果 |
| --- | --- |
| `make test TESTS='tests/test_analysis_dsl_s3_p4.py'` | **通过**：1 passed。 |
| `make test TESTS='tests/test_analysis_dsl_fixtures.py tests/test_analysis_dsl_contracts.py tests/test_analysis_dsl_exchange.py tests/test_analysis_dsl_execution_identity.py tests/test_analysis_dsl_j1_construction.py tests/test_analysis_dsl_j1_source.py tests/test_analysis_dsl_j1_artifact.py tests/test_analysis_dsl_s2_p1.py tests/test_analysis_dsl_s2_p2.py tests/test_analysis_dsl_s2_p3.py tests/test_analysis_dsl_s3_p1.py tests/test_analysis_dsl_s3_p2.py tests/test_analysis_dsl_s3_p3.py tests/test_analysis_dsl_s3_p4.py tests/test_semantic_live_registry.py tests/test_cutover_documentation_examples.py tests/test_lazy_correlation_numeric.py tests/test_lazy_local_placement.py'` | **通过**：216 passed；Runtime 标记被默认门禁排除。 |
| `make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py tests/test_analysis_dsl_j1_artifact.py tests/test_analysis_dsl_s2_p1.py tests/test_analysis_dsl_s2_p2.py tests/test_analysis_dsl_s2_p3.py tests/test_analysis_dsl_s3_p2.py tests/test_analysis_dsl_s3_p3.py'` | **通过**：18 passed，包括冷恢复、固定命中和发布故障。 |
| `make typecheck TYPECHECK_TARGETS='tests/typing/analysis_dsl_internal_contract.py marivo/analysis marivo/semantic'`；`make lint-agent LINT_TARGETS='tests/test_analysis_dsl_s3_p4.py'` | **通过**：281 个源文件类型检查；P4 新测试格式、Ruff 与导入契约通过。 |
| `make check-agent`；`git diff --check` | **通过**：855 个文件格式检查、Ruff/导入契约、372 个源文件类型检查、5161 passed / 4 skipped、API docs built；差异无空白错误。 |

新测试首次定向 lint 因 Ruff 格式失败；对该文件运行
`.venv/bin/ruff format tests/test_analysis_dsl_s3_p4.py` 后定向 lint 通过。
功能拒绝的复现入口为 `s3_p1` 的缺键/非法 Cell、`s3_p2` 的坏 receipt
与提交前故障、`s3_p3` 的来源执行失败不兜底；本轮集成重跑未发现功能失败。

成本复测命令为
`PYTHONPATH=. .venv/bin/python devtools/analysis_dsl_s3_p3_cost.py --all`。
每个规模/布局在独立进程测量同一 August 事实及 Entity 范围；
`fixed_pandas` 包含同次成员捕获与两端 Artifact 发布，另两条包含结果发布；
最后的展示读取不计时。MB 为十进制，RSS 为进程峰值，秒数为单次观测。

| 事实 / 成员 | 布局 | 查询 / 批次 | 交换 MB | Artifact 读 / 总物化 MB | 完整端点 Arrow MB | 峰值 RSS MB | 秒 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 / 250 | 来源数值 | 3 / 3 | 0.002 | 0 / 0.003 | 未收集 | 292.3 | 0.188 |
| 1,000 / 250 | 先物化后 pandas | 9 / 9 | 0.036 | 0.004 / 0.006 | 0.028 | 288.5 | 0.294 |
| 1,000 / 250 | 来源→Python | 9 / 9 | 0.036 | 0 / 0.003 | 0.028 | 288.0 | 0.214 |
| 100,000 / 25,000 | 来源数值 | 3 / 27 | 0.242 | 0 / 0.003 | 未收集 | 335.6 | 0.294 |
| 100,000 / 25,000 | 先物化后 pandas | 9 / 81 | 3.714 | 0.103 / 0.105 | 2.862 | 360.6 | 1.484 |
| 100,000 / 25,000 | 来源→Python | 9 / 81 | 3.714 | 0 / 0.003 | 2.862 | 346.8 | 0.662 |

100,000 事实时 reader 最大批次为 1,024 行，确实分批；Python 数值路线
仍收集 2.862 MB 两端 Arrow Table，并与 pandas/NumPy/SciPy 工作区共存。
固定路线 Arrow 后/数值前/数值后的 RSS 为 342.6/360.4/360.6 MB，
来源→Python 为 341.9/341.9/346.8 MB；固定路线 pandas 转换至少
复制 2.862 MB 端点 payload。RSS 不能精确分摊各 native 库；批次大小
不是算法内存上界。来源数值不收集完整端点，仍收集 25,000 个成员。
没有预算准入、隐式抽样、截断、spill 或自动近似。

相对 S2 P4 代码基线 `eda6e01bfc6c`，S3 P1–P3 已提交范围为 24 个
文件、2,661 行新增、94 行删除（`git diff --stat/--numstat
eda6e01bfc6c..HEAD`，限 `marivo/analysis marivo/semantic tests docs/specs
docs/superpowers/specs devtools`）。P4 另新增一个定向测试文件；这些行数
不包括忽略目录的验收文档。

| Requirement | Owner → 实现位置 | 回归证据 | 适用方法 |
| --- | --- | --- | --- |
| 同次双端完整域与输入绑定 | Semantic 声明；`observation/dsl_j1.py` 私有构造；`compiler/dsl_j1_source.py` 的 Ibis 准备 | `s3_p1`、`s3_p4` | J4；复用 J2 多前驱共享 |
| 数值资格、平均秩、计数与状态 | 既有 Association registry/contract；`operators/association_values.py` | 独立 oracle、并列值/Null/常量/不足对及双路线对照 `s3_p1`/`s3_p3` | J4 Spearman |
| 物理路线与失败隔离 | `compiler/placement.py`；`materialization/dsl_j4_source.py` 的 Ibis/Python 适配 | 真实 DuckDB、能力桩、失败不兜底 `s3_p1`/`s3_p3` | 当前 DuckDB 两路线 |
| 受控交换、发布、固定续算与恢复 | 原 BatchStream/codec/Store；`materialization/dsl_j1_artifact.py`、`dsl_j1_runtime.py` 的 J4 接入 | `exchange`、`s3_p2` 及 Runtime 冷恢复 | J1–J4 已承诺 K |
| 披露及验证 | `docs/specs/analysis/operators-and-frames.md` 的私有边界；S3 测试与成本脚本 | 本节矩阵、216 默认/18 Runtime、成本复测 | 私有 S3；公开披露交 S4 |

内核变化增加的是多端同次绑定、J4 状态/计数的受控表示与来源数值资格；
没有第二套 AST、Store、registry 或手写产品 SQL。Ibis 来源数值与 Python
数值分别实现平均秩/相关计算，`s3_p3` 的同向量对照是持续防漂移的必要检查。
来源准备的共同键域检查与固定 Artifact 的绑定/receipt 检查属于不同边界，
不能只因结果键相同而合并。其他后端的能力注册、SQL 语义及资源关闭资格
仍须独立验收，不能由 DuckDB 或能力桩推定。

S3 完成标准中的 J4 全来源链、发布后 pandas 筛选、同次双固定输入及断源
冷恢复、J1–J3 无回退、V01–V13/V15 首轮必要项、批次/路线/资源和成本记录
均满足。V14（公开 typing、Help/contract、用户脚本和真实 Agent）、公开
Analysis DSL 激活、远端资源与其他真实后端资格，以及完整公共矩阵均为
**未验证**，交 S4 或后续独立阶段。

## 15. S4 P3 安装后用户脚本与真实 Agent 验收

**范围：仅 S4 P3。** P1/P2 基线为 `panda` HEAD
`c23b3b6b6362e6a47a610f9e170821396c4ba568`，实施前跟踪工作区干净。
本节不把 §14 私有矩阵或 P1/P2 单元测试当作用户/Agent 验收；P4 全公共矩阵、
其他真实后端、远端资源与发布仍未验证。本节的项目、wheel、完整 Agent 轨迹、
最终代码、输出、oracle 和诊断保存在本地 Git 忽略的
`docs/superpowers/plans/s4-p3-evidence-{c23b3b6,help,timefix,final,final-retry,tzfinal,axisfinal,axisverified,axisverified-retry}/`；
该本地证据不等于已提交或可远端取得的附件。

实施资源为 macOS、Python 3.12.13、`marivo` 0.5.3.dev0、Ibis 12.0.0、
DuckDB 1.5.5、pandas 2.3.3、PyArrow 25.0.1、SciPy 1.18.1、NumPy 2.5.3。
各题用户脚本和 Agent 使用相同事实内容的独立 DuckDB 项目，正式
`models/datasources` 与 `models/semantic` 声明，项目级 Store；
`MARIVO_PROJECT_ROOT` 指向各自项目，`MARIVO_TELEMETRY=off`，无 MinIO、
远端来源、测试夹具导入或私有 DSL 构造。四题原始事实 SHA-256 依次为
`4602bb83be4abf28db4f2d2d59dfd76e628b8595ee46b0da30957cdc7838f1e0`、
`8bd6bff6e1988fce3e88276e98d06781be395f62361bec8e377e352ab7a89de9`、
`acf02e9f6028539935325d887e1ebb145f60be2527af4b2561e6e402522f9c75`、
`ac5e885b01e00a69a848b1e013669873fc70e0e159e65549912e2b9fe007e62d`。
项目文件逐份 digest 见 `projects.json`；因为 datasource 声明含各项目的
绝对数据库路径，文件 digest 可不同，事实 digest 相同。

时间区修复前的 wheel 为
`/tmp/marivo-s4-p3-wheel-final/marivo-0.5.3.dev0-py3-none-any.whl`，
SHA-256 `542ae17741ead443b4d8fe8a7cd80fb0f43d30d8e6e934d0ec3ee2a977249660`。
安装到 `/tmp/marivo-s4-p3-venv-final/`；`installation.json` 证实
`marivo.__file__` 与 `marivo.analysis.__file__` 均位于该环境的
`site-packages`，`direct_url.json` 指向此 wheel，而非仓库源码。
报告时区首次修复后的 wheel 为
`/tmp/marivo-s4-p3-wheel-tzfinal/marivo-0.5.3.dev0-py3-none-any.whl`，
SHA-256 `63d1f6e7b96ee80a50526fee0bf08f1375b02f9509865768d1cdbe4f0df4a3e0`，
安装在 `/tmp/marivo-s4-p3-venv-tzfinal/`。随后把 J1/J3 边界转换目标从写死的 UTC
改为正式 Semantic 时间维度中经准入验证的时区；report 与时间维度时区同名时
保留原边界，不做转换。最终 wheel 为
`/tmp/marivo-s4-p3-wheel-axisverified/marivo-0.5.3.dev0-py3-none-any.whl`，
SHA-256 `6f466cab568788740224d05492af6db4b266ea3f1e91f619c5b439115d5388e6`，
安装在 `/tmp/marivo-s4-p3-venv-axisverified/`。`axisfinal` Agent wheel SHA-256
为 `cacf9128c2f0f38749c99523e8833bfdd2aa836ea5db761f0e6c240184d0c000`；
它与最终 wheel 的唯一产品代码差异是未触发的非 UTC 准入错误 repair 文案，
最终 wheel 增加了不能误声明来源时区的提示。此前首次 wheel SHA-256 为
`122c99c9356e66ee8734abb2edb79fa953ada603ee5c47300b6408b95d5e3af5`；
中间修复时间 Help 的 wheel SHA-256 为
`d452be1f33b4f0e8dbcbf01f06a634097608bfffa7f77ba56ee8423c2231c033`。
记录每次 Agent 尝试所用 wheel，不能交叉套用结果。
最终 wheel 安装环境中的 `dsl_j1.py` 与工作区该文件 SHA-256 同为
`eed0adcdb361a9114c909d0859ff6a5a1180d69e2c85c9e7444e90c2774752b5`；
`installation.json` 的模块路径和 `direct_url.json` 也确认了隔离安装。

复现入口为 [`devtools/analysis_dsl_s4_p3/README.md`](../../../devtools/analysis_dsl_s4_p3/README.md)。
先执行 `uv build --wheel --out-dir ...`、`uv venv ... --python .venv/bin/python`、
`uv pip install --python ... '<wheel>[duckdb]'`，再用
`.venv/bin/python devtools/analysis_dsl_s4_p3/controller.py prepare ...` 与
`controller.py scripts ...`。控制器逐题调用已安装解释器；独立 oracle
直接从原始事实用 DuckDB SQL 与平均秩算术求值，不导入 Marivo。每题另将
`warehouse.duckdb` 临时断开，在新进程用 `session.resume` 和
`session.artifact` 精确恢复并继续 K；J1 额外证明新来源求值报
`DatasetConstructionError`（`unavailable datasource warehouse`）后，旧
Artifact 仍以原 ref 恢复且上卷为 1000。复核记录在各 wheel evidence 的
`user/`、`oracle/`、`recovery/` 和 `installation.json`。

| 题目 | 用户脚本（`6f466cab…` wheel） | 独立 oracle 与 Artifact/Cell/K 证据 | 真实 Agent |
| --- | --- | --- | --- |
| J1 | **通过** | 总计 1000；east 600、south 400、west Null/empty_contribution；east web 450、mobile 150；旧 Artifact 断源后同 ref 上卷 1000。 | **通过**：`axisfinal/agent/j1/attempt-06` UTC 新会话得全部目标值及 Cell；22 次 contract、零越界请求、同 wheel 重放通过。 |
| J2 | **通过** | Aug−Jul：A −40、B +20、C −50、D 0；严格下降域 A/C；九月均值 15，C 的零贡献为 Defined；断源后同 ref 可筛选并取成员。 | **通过**：`axisfinal/agent/j2/attempt-06` UTC 新会话得 A/C、15；6 次 contract、零越界请求、同 wheel 重放通过。 |
| J3 | **通过** | 组件上卷：web 160/3、mobile 0、总体 160/4=40；当前 3 行均值 130/3；保留 5 个部件、Defined Cell、断源后同 ref 可上卷。 | **通过**：最终 wheel `axisverified-retry/agent/j3/attempt-07` 独立 UTC 会话得全部目标值与域、区分组件 `rollup` 和当前行 `summarize(mean)`；34 次 contract、零越界请求、同 wheel 重放通过。前两次失败仍保留。 |
| J4 | **通过** | Spearman −0.4；输入/匹配/完整对 4/4/4、Null 0、status valid；负值筛选 1 行，断源后同 ref 可筛选。 | **通过**：`axisfinal/agent/j4/attempt-05` UTC 新会话得 −0.4、4/4/4、valid 与负值筛选 1 行；12 次 contract、零越界请求、同 wheel 重放通过。 |

报告时区回归另以同一 J1 来源验证：UTC Semantic 事件轴配
`Asia/Shanghai` report 时区得 677，配 `America/Los_Angeles` 得 649；
两种情况下，成员 DSL 与原 Dataset 路线一致。aware UTC 起止时刻均得 1000；
report 与已准入事件轴同为 UTC 时保留原边界。这只证明当前 UTC 事件轴
资格内的同区/异区解释，不扩大到非 UTC 或由物理字段、datasource 默认时区
推导的成员 DSL 来源资格；该资格仍明确拒绝，不能据此宣称所有来源时区已支持。
最终定向门禁：`make runtime-test TESTS='tests/test_analysis_dsl_public.py'`
17 项通过；`make test TESTS='tests/test_analysis_dsl_p2_disclosure.py tests/test_analysis_help_resolution.py tests/test_unified_help.py tests/test_lazy_predicate_algebra.py'`
95 项通过；对所有触及的 `marivo/analysis` 模块与 P3 devtools 的
`make typecheck TYPECHECK_TARGETS=...` 通过，`make lint-agent LINT_TARGETS=...`
通过；`site/` 的 `npm run build` 构建 321 页并验证中英文安装脚本；
`git diff --check` 通过。命令参数与可复现路径见 P3 `README.md`。

Agent 由已登录的 Claude CLI 2.1.186 以每题独立项目和新 session ID
运行；CLI 当前实际调用模型在轨迹中报告为
`zai-messages/glm-5.3-flashx`，不能称为 Anthropic 模型。提示仅给业务问题、
已安装解释器、正式声明、UTC 报告日历要求和从 `marivo.help()` 逐级发现的入口；无用户脚本、
oracle、目标答案、私有实现或费用上限。`agent_trial.py` 保存精确提示、
CLI flags、完整 stream JSONL、stderr 和 session ID；`audit_trace.py`
归档 Agent 自写的 `answer.py`、检查 Help/contract 使用及越界访问，
`replay_agent.py` 保存同一 wheel 的实际 stdout/stderr。人工复核输出、
时间域、代码中的硬编码值和是否从公开对象继续操作；自动审计零越界
请求不能替代轨迹人工审查。
早期 Agent 提示未指定 report 时区；按 Session 契约，这会取执行环境系统时区，
不能拿 UTC oracle 直接判定其日历月份。`axisfinal` 起的提示把 UTC 作为业务报告日历
明确给出，Agent 仍须从公共 Help 自行发现 Session 参数和分析路径；此前轨迹保留。
首次启动使用空隔离 CLI 配置而未继承登录态，直接返回认证失败；失败
`trace.jsonl`、`stderr.txt` 留在首次 evidence 的 `agent/j1/` 根部。
驱动器随后改为先用原有登录态执行 `claude auth status`，并仅把
`MARIVO_PROJECT_ROOT`、`MARIVO_TELEMETRY` 等必要环境交给 Agent；
此认证预检失败不计任何 J 题业务验收。意外生成的 CLI 机器/用户标识配置
已从 evidence 清除，不复制密钥、账号标识或其他敏感配置。

首次试用揭示两类披露缺口。`analysis.entry` 原先优先展示旧
`session.observe` 路线，且 `analysis.time_scope` Help 未直接说完整月份
应以次月首日为右开结束；Agent 的 J1/J3/J4 首次脚本均将 8 月结束设为
`2026-08-31`。J1 首次仅得 600、漏掉 8 月 31 日 south 400；J3/J4
虽因夹具时点数值巧合与 oracle 相同，时间域仍错误，均判失败。
入口导航修复后 J1 第二次仍选错结束日；补充右开 Help 后的第三次
J1 得到全部正确值，但 `answer.py` 的断言硬编码 `600`，按计划的
“不依赖硬编码预期”仍判失败并保留最终代码与轨迹。
J2 首次虽然得 A/C、15，却沿旧 Dataset 路线从 `to_pandas()` 手工筛选
成员，未完成规范成员 DSL 链；不得以正确数值记为 Agent 通过。
其轨迹还实际复现旧 Dataset `where(delta < 0)` 在 Ibis 12 上访问
`Int64.bit_width` 的 `AttributeError`。局部修复改用 Ibis 整数类型的
`bounds.lower/upper`，定向 DuckDB 回归通过；最终 wheel 上同一公开
Delta 筛选返回 A/C 与 −40/−50，见 `legacy_delta_filter.json`。
这些首次失败未被人工改写为成功结果。

已完成的中间 wheel 真实 Agent 路线：J2 自行使用
`session.members → observe → compare → where → members → observe → summarize(mean)`，
得到 A/C 与 15；J4 自行使用 `session.members → 两端 observe →
correlate(spearman) → coefficient.where`，得 −0.4、4/4/4、valid 和 1 行。
两题 `audit.json` 中越界请求均为空，Agent 使用公共 Help/contract，
最终代码归档并在同 wheel 上重放通过。

最终明确 UTC 的 `axisfinal` 新会话各自拥有独立项目、session ID、提示与完整
JSONL；J1/J2/J4 的 `audit.json` 分别记录 22/6/12 次 `contract()` 调用、
零直接数据库或私有实现请求，`replay_agent.py` 在原 wheel 上均退出 0。
J1 正确区分 west 的 Null/`empty_contribution` 与数值零，并用成员上卷
交叉验证总数；J2 用严格负 Delta 保留 A/C，九月 Defined 零纳入均值；
J4 报告 −0.4、4/4/4、valid 并以 typed `where` 选取 1 行。
J4 Agent 还在重复求值中观察到系数 double 末位约 1–2 ulp 的差异；
本次验收只据符号、status、pairing 与容差内系数判定，不把浮点位级相同
冒充契约。J3 的 `axisfinal` 结果数值和概念路径正确，但解释输出中写死
“single mobile order”，即使旁边打印的 `n_mob` 由结果求得，也违反本轮
“解释数量从结果推导”的 Agent 判据。`axisverified` 的第一次 J3 重试也
在 `answer.py` 中断言 July 订单数等于固定 0，并将这件事称为八月窗口绑定
证明；这既硬编码预期，也不能推出该结论。两次均原样存档。后续新会话只
增补不透露答案的验证纪律：检查应比较公开结果，解释数量须由变量生成。
第三次 `axisverified-retry/agent/j3/attempt-07` 使用最终 wheel、相同事实的
新项目和全新 Claude session，最终 `answer.py` 的 8 个一致性检查均只比较
公开执行结果；渠道/客户×渠道域、比值、组件重组、当前行均值均对照 oracle
一致，报告 Artifact refs，`audit.json` 记录 34 次 `contract()` 调用、
101 次工具调用及零越界请求，同 wheel 重放退出 0。最终自然语言有一处
“both CNY” 单位括注不严谨；表头和方法明确写成每订单收入，归档原文且
不人工改写。此瑕疵不改变本轮列明的数值、域、Cell、Artifact、可继续操作、
公开路径及 `rollup`/`summarize` 判据，J3 Agent 标为通过。

## 16. S4 P4 公共矩阵回归、增量成本与交付判定

**状态：P4 首轮适用公共项通过。** 基线为 `panda` HEAD
`5df3414e2d34727a0b464f1928b51391ee571388`，开始时跟踪工作区干净。
P4 未改产品执行代码；新增公共 V13 重命名回归与公开入口成本测量器，用户脚本、
安装包、矩阵及门禁证据保存在本地忽略目录
[`s4-p4-evidence-5df3414`](s4-p4-evidence-5df3414/)。

环境为 macOS、Python 3.12.13、Marivo 0.5.3.dev0、Ibis 12.0.0、
DuckDB 1.5.5、pandas 2.3.3、PyArrow 25.0.1、SciPy 1.18.1、NumPy 2.5.3。
当前 HEAD 构建 wheel SHA-256 为
`48b393c3accce8a20c0fe303941d5ec85e9c627e851c46d38821cabd40df95ee`；
`installation.json` 证实 `marivo` 与 `marivo.analysis` 从隔离环境的
`site-packages` 导入，`direct_url.json` 指向该 wheel。P4 后续改动只涉及
测试和 `devtools`，未改变 wheel 中的产品模块。数据项目使用本地 DuckDB、
正式 datasource/semantic 声明、项目级 Store；未启动 MinIO 或远端服务。

当前安装包的用户旅程由独立控制器逐题运行、以源事实 SQL/平均秩 oracle 核对，
并在新进程断开 DuckDB 后恢复 Artifact 继续 K：

| 旅程 | 公共脚本与独立 oracle | 冷恢复 |
| --- | --- | --- |
| J1 | **通过**：总收入 1000；地区与渠道成员域、Null/empty 状态一致。 | **通过**：断源后沿原 ref 上卷仍为 1000。 |
| J2 | **通过**：A/C 严格下降，九月均值 15，零贡献仍为 Defined。 | **通过**：断源后沿原 ref 筛选并读成员。 |
| J3 | **通过**：总体比率 40 与当前三行均值 `130/3` 区分；多根组件及五个部件一致。 | **通过**：断源后沿原 ref 上卷。 |
| J4 | **通过**：Spearman −0.4，完整配对 4/4/4，状态 valid。 | **通过**：断源后沿原 ref 筛选。 |

复现命令为 `.venv/bin/python devtools/analysis_dsl_s4_p3/controller.py prepare
--workspace /tmp/marivo-s4-p4-projects --evidence
docs/superpowers/plans/s4-p4-evidence-5df3414`，以及同一控制器的 `scripts`
动作，传入 `/tmp/marivo-s4-p4-venv/bin/python` 与当前 wheel。逐题 stdout、
oracle、recovery 与项目 digest 均在该证据目录。真实 Agent 的单独试用不在 P4
重复执行：S4 P3 §15 已对每题保留原始轨迹、最终代码、oracle 和同 wheel 重放；
P4 没有修改产品或披露行为。

| 项 | P4 公共状态与证据 | 明确边界 |
| --- | --- | --- |
| V01 组合 | **通过**：安装后 J1–J4 公开脚本及公共 Runtime 用例对照独立 oracle。 | 仅首轮 J1–J4。 |
| V02 完整域与多根 | **通过**：公开 J1 成员域、J3 多贡献根、完整组件上卷均与 oracle 一致。 | 限已声明 Entity/Relationship 路径。 |
| V03 状态与空输入 | **通过**：公开空/Undefined、Defined 零值、Null/empty contribution 及 J3 原状态上卷通过。 | Association 不作总体 rollup。 |
| V04 拒绝 | **通过**：公共错误测试覆盖错成员/单位、缺键/关系、混合 live/fixed、丢失部件与不匹配 snapshot。 | 未扩展到未来方法。 |
| V05 双实现 | **通过**：公开来源执行及固定 Artifact 的 pandas 筛选/上卷均从唯一 DSL 入口执行。 | J4 来源数值与 Python 数值核的同向量双路线对照仍以 S3 私有矩阵为证；不宣称公开强制选路。 |
| V06 交换与 codec | **通过**：公开 Artifact、绑定 snapshot、receipt 与恢复路径实际往返。 | codec 的类型/漂移/损坏全向量仍引用 S3 §14 私有测试。 |
| V07 pandas 与冷恢复 | **通过**：安装后 J1–J4 均在新进程断源恢复并继续合法 K。 | 不从 J4 旧系数重算秩。 |
| V08 身份、命中与混合 | **通过**：公共用例重跑来源、固定命中、失败保留旧 Artifact 与混合早拒绝。 | 不声称跨独立来源查询的事务快照。 |
| V09 数值边界 | **通过**：公开 J1–J4 主要结果、配对计数、Null 与零值经过 oracle 核对。 | 并列/非有限等完整数值拒绝向量由 S3 §14 私有测试补足。 |
| V10 惰性、共享与原子性 | **通过**：公共 compare/observe 图、失败保留与 cold Artifact continuation 通过；已有同次共享和提交故障证据继续引用 S3 §14。 | 不扩大跨查询事务保证。 |
| V11 放置 | **通过（DuckDB）**：当前公共 source adapter 的准入路线在真实 DuckDB 上运行；私有能力桩及失败不回退沿用 S3 §14。 | 其他真实后端 **未验证**；能力桩不等于后端资格。 |
| V12 分批与成本 | **通过**：`devtools/analysis_dsl_s4_p4_cost.py --all` 以公共 `Session → observe → correlate → execute` 路径测量。 | 每规模单次本机观测，无性能阈值；峰值 RSS 不是算法内存上界。 |
| V13 扩展代价 | **通过**：[`test_public_j4_renamed_entity_and_fields_keep_the_public_route`](../../../tests/test_analysis_dsl_public.py) 重命名 domain、Entity、关系、Metric、物理表/字段并改单位为 kWh，公开 J4 仍得 −0.4/4 对。 | 只证明同一注册 owner 路径可重用，不计通用扩展工时。 |
| V14 公开披露与 Agent | **通过**：S4 P2 Help/type/doc 与 S4 P3 四题 Agent 证据见 §15；安装包逐题重放及 public contract 使用有独立归档。 | Agent 的限定范围与系统/模型信息按 §15 原样保留。 |
| V15 SQL 委托 | **通过（DuckDB）**：当前公共 route 经 `public_j1_source` 使用 Ibis 后端，安装后查询与独立 oracle 一致。 | 其他 SQL 方言/真实后端 **未验证**。 |

公开成本单次复测输出如下。每个规模为独立进程、新项目；读数来自公开 Session
执行，适配器只包装 Ibis reader 计数。固定 continuation 为 Artifact 系数筛选，
当前合成系数为正，因此负值筛选结果为零行。

| 事实 / 成员 | 公共来源执行 | 固定 continuation | 来源查询 / 批次 | 转移行 / 字节 | 最大批次 | 峰值 RSS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 / 250 | 0.896 s | 0.057 s | 3 / 3 | 252 / 2,010 B | 250 | 286.7 MB |
| 100,000 / 25,000 | 0.935 s | 0.058 s | 3 / 27 | 25,002 / 242,103 B | 1,024 | 336.4 MB |

公共验证命令及结果：

| 命令 | 结果 |
| --- | --- |
| `make runtime-test TESTS='tests/test_analysis_dsl_public.py'` | **通过**：18 passed，包括 V13 重命名公共路线。 |
| `make test TESTS='tests/test_analysis_dsl_public.py tests/test_analysis_dsl_public_static.py tests/test_analysis_dsl_p2_disclosure.py'` | **通过**：3 passed；Runtime 由上一行单独运行。 |
| `make check-agent` | **通过**：5165 passed / 4 skipped，lint/import contracts、377 源文件 typecheck、API docs build。 |
| `make typecheck TYPECHECK_TARGETS='devtools/analysis_dsl_s4_p4_cost.py'` | **通过**：新成本工具 typecheck。 |
| `npm run build`（`site/`） | **通过**：Astro 0 error/warning/hint、321 pages；英文与中文安装脚本校验通过。 |
| `git diff --check` | **通过**：最终工作树无空白错误。 |

另试对整个既有 `tests/test_analysis_dsl_public.py` 作 mypy 定向检查；它连同被导入的
`tests/shared_fixtures.py` 报出 9 条历史类型诊断（涉及旧负例调用、旧 fixture
返回注解/参数）。新增 V13 测试块无对应诊断；仓库规定的 `make check-agent`
类型范围通过。该定向扩展失败作为既有测试 typing 欠账记录，不掩盖为类型门禁通过。

S4 P4 的公共必要项已按当前资格边界复核。公开 DSL、V14 Agent、首轮 DuckDB
技术矩阵分别有安装后公共证据；其他真实后端、远端资源及发布资格仍为
**未验证**。这不代表完整后端矩阵通过，也不代表已发布。
