# 全量分析代数与 Analysis DSL 重构阶段验收主记录

Date: 2026-09-26

Status: R0.1–R0.5 的静态产物已登记；R0.5 替代路线可行性和运行资格未验证，R0.6 未开展，R0 整体未验收。

本文件按[主计划](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)和[R0 实施文档](2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md)续记实际证据。历史验收不自动转成新 DSL 的技术、后端、安装包或真实 Agent 资格。

| R0 出口 | 状态 | 当前证据或缺口 |
| --- | --- | --- |
| R0.1 工作基线、输入 hash、历史证据边界 | **通过（索引与归档）** | [R0.1 索引](2026-09-26-marivo-full-refactor-r0-evidence-index.md)与 [manifest](evidence/r01/manifest.json)；S0/组合正文及 P4 小型机器结果已复制。S4 原轨迹与 wheel 为本机专有历史附件，未授予新资格 |
| R0.2 C01–C18 与消费者反查 | **通过（静态反查）** | [能力台账 §1–§4](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#1-本次可复核快照与读法)列出 C01–C18、44 个现有子单元、导出/Help、双执行链、主要消费者与去向；目标运行资格仍未验证 |
| R0.3 必需 owning spec 决定 | **通过（目标契约文档；C01.c 已按用户决定修订）** | [Analysis R0.3](../../specs/analysis/python-analysis-design.md#r03-accepted-full-algebra-target-inactive)、[Semantic R0.3](../../specs/semantic/semantic-object-model.md#r03-full-algebra-target-decisions-inactive)、[Datasource SQL 目标](../../specs/semantic/datasource-layer.md#r03-target-ibis-owned-analysis-reads-and-terminal-raw-sql)及[能力台账 §5](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#5-r03-接受的契约索引)：C18、权重/参照、普通 ratio、业务顺序、原状态/当前行及 SQL/provenance 已给 typed 目标、Cell/部件/错误/K/反例。`md.raw_sql`/`RawSqlResult`/Help 保留为终端只读逃生通道，不能重入 Analysis；其他新 DSL 目标尚未实现 |
| R0.4 规则、方法、模块责任 | **通过（规则和迁移台账）** | [能力台账 §6](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#6-r04-六类元算子规则冻结)：六类元算子含 Pre、RequiredParts、PartTransform、Post、Transport、Eval；44 个子单元及展开方法记录版本、K、独立 oracle、阶段；§6.2 登记旧实现/消费者同迁删除点。新规则尚无产品执行证据 |
| R0.5 SQL/adapter/六后端目标资格 | **静态台账通过；替代可行性阻塞、运行未验证** | [SQL/adapter 台账](2026-09-26-marivo-full-refactor-r0-sql-ledger.md)列 DS01–DS21、AN01–AN33 的构造/调用/提交与 Ibis/驱动替代，Store SQLite 事务白名单、七项 adapter 责任及六后端目标矩阵。DS02 是具名公共终端 SQL 路线；其他内部 SQL 例外为空。DS11/DS15 等控制/认证操作尚无已证等价驱动 API；六后端、表形态、数值/时间和资源格仍未实跑 |
| R0.6 破坏性变更与 R1/R2 交接 | **未验证** | 目标入口、消费者及验收索引尚未收束 |

R0 整体不得标为通过。R0.1 没有改产品代码，也没有重跑旧 Runtime、安装包或 Agent；可移植原始 Agent 轨迹与 wheel 缺口在索引 §6 记录为历史待补证。R0.2 在 panda 的代码 HEAD 为 `d5e06022c7fcd355b2a31ab6935aea593b60f0d1`，只执行静态扫描并编写文档，未运行产品测试或远端后端。R0.3–R0.5 按用户指定直接在干净的 `panda` 起点实施，未创建隔离工作树，未改产品代码、packaged skills 或 `AGENTS.md`。

## R1.1 实施中记录

起点仍为 `panda`、HEAD `1bd9b7e98a3c9f7878de73e07f89aeabdc740073`；R1 实施文档在起点为未跟踪文件，未由本工作修改。R0.6 的交接未完成，R0.5 控制/认证替代路线尚未实证，R0 整体仍未验收；R1.1 按可独立部分推进。

已落地：engine provider 按选中后端延迟装载；同一 provider 打开内部 `SourceSession`，会话签发的 Ibis 编译读取绑定来源、用途和 schema，伪造/跨会话句柄被拒；本地 DuckDB/SQLite 基础 scan/filter/project/group/count 的真实游标输入、空流 schema 与提前关闭已在定向测试覆盖。`md.test`、`test_no_persist` 与 Semantic source-health 的连接探测已改为 provider 提交 Ibis literal。旧 ClickHouse `postprocess_sql` 目标已删除。单表、无时间范围的 DuckDB/SQLite Population 扫描/过滤及 sum/count Metric 聚合（含直接维度分组）现由 `DatasetRuntime` 调用 `SourceSession` 编译、读取、验证后交给现有发布协议；Population 与基础 Metric 定向测试在旧 DuckDB `statement(sql)` 被钉死时仍可成功。其余旧 Dataset 来源步骤在 Run 与来源读取前结构化拒绝。共用 `ExecutionAdapter` 协议已删除 `statement(sql)`；旧 Runtime resolver 只保留 R4 固定 Artifact 的 DuckDB 实现，`prepared_source` 对旧来源绑定另有阻断。

尚未闭合：旧具体后端类还保留 `statement(sql)` 和 AN01–AN33 文本实现；其来源入口虽被阻断，仍需按迁移阶段删除。基础消费者仅获单表、无时间范围的本地资格；带时间范围、fold、mean、`metric.where` 与其他领域方法仍阻断。全量默认测试门禁仍因这些旧正例和以其建立 Artifact 的续算测试失败，R1.1 **未通过**。PostgreSQL、MySQL、Trino、ClickHouse 的基础读取和六后端表形态/精确类型/取消格均**未验证**；SQLite 只证明本地测试连接上的批次语义，不证明只读控制能力。`md.raw_sql` 仍为独立公共终端，但其完整 R1.3 边界尚未在本轮重新验收。不得把本节的 provider 注册或本地测试写成 R1 出口通过。

| 旧文本路线（SQL 台账） | R1.1 当前处置 | 后续迁移阶段 |
| --- | --- | --- |
| AN01 数值宏、AN31 时间 lowering | 基础单表 sum/count Metric 聚合已走 `SourceSession`；其余旧 Metric 来源路线在 Run 前阻断。旧具体后端文本实现尚未删除，共用 `ExecutionAdapter.statement` 已删除 | R5 方法表达式、R9 后端资格 |
| AN02–AN09 Event/Lifecycle replay、bundle 与检查 | 来源型旧 Dataset 路线在 Run 与来源读取前阻断；旧实现尚未删除 | R7 方法实现，R9 后端资格 |
| AN10 Lifecycle history 汇总 | 来源型旧 Dataset 路线已阻断；旧私有检查尚在 | R7 方法实现 |
| AN11–AN12 归因 SQL 校验 | 来源型旧 Dataset 路线已阻断；旧文本构造和提交尚在 | R6 方法实现，R9 后端资格 |
| AN13 来源计数 | `local_stage._source_count` 已改为 Ibis count 表达；基础单表 count 聚合由新 session 读取，其他旧 Dataset 来源路线仍在 Run 前阻断 | R5/R9 复核实源资格 |
| AN14 私有成员完整性 | DuckDB 手写 SQL 已改为 Ibis 检查；旧 adapter 的其他文本入口仍在 | R4/R9 复核 receipt 与实源资格 |
| AN15–AN30、AN32 控制、metadata、值校验和认证 | 旧 Dataset 来源路线在 Run 前阻断；其他控制和 metadata 使用点未闭合，不转授本地基础读资格 | R1.2/R1.3；涉及领域校验再由 R9 复核 |
| AN33 共用 `statement(sql)` | `ExecutionAdapter` 协议已删除该方法；来源 resolver 不再返回旧具体后端，只有 R4 固定 Artifact 的 DuckDB 实现仍可由旧 resolver 获得。旧具体后端的文本方法和直接测试仍在，未算完整删除 | R1.1 清理旧具体类；R4 接收新传输契约 |

本次本地证据固定在 `panda` HEAD `1bd9b7e98a3c9f7878de73e07f89aeabdc740073` 的未提交工作树：代码、测试及受影响文档的受跟踪改动（不含本验收文件）的 `git diff --binary HEAD` SHA-256 为 `63fdb1c25f7353ffcf021d307dfc4669361e27e10626614ef1a4269c9ab94789`；新增 `adapters.py`、`basic_source.py`、契约测试和旧路线阻断测试的文件 SHA-256 依次为 `a2a3306ad7b742772d9db73a1addab3a8065e65c2f7df803ca329d3ed3da8ce0`、`79bb966d62a252eb1013076a2f8ff1e25e291e0a3a2e9fb49bd8b32bb58ccf6b`、`3a88c1eb9257cc2e3f048de574eafc271847414016c9ca602f17c83174d90e4b`、`04364a307c283e511a876b01d56197803a3a516660e8e13bd079fce526fd35df`。这些 hash 只定位本次候选，不等于阶段验收。

实际执行：本轮 `make test TESTS='tests/test_lazy_contract_source_admission.py tests/test_datasource_adapter_contract.py tests/test_lazy_inspection_boundaries.py tests/test_lazy_backend_dispatch.py tests/test_lazy_postgres_errors.py'` 为 65 通过，覆盖基础 Population/Metric 的静态准入披露、来源身份、嵌套值精确解码和旧 resolver 边界；`make runtime-test TESTS='tests/test_r11_legacy_domain_block.py'` 为 11 通过，包含旧 Event/Lifecycle/Attribution/非基础 Metric/Association 阻断，以及 Population 扫描/过滤、sum/count Metric 聚合与直接维度分组的会话读取。前轮其他 datasource/raw SQL 定向结果仍列在本节较早候选，不能替代这轮门禁。最新 `make check-agent` 的 lint/import contract 与 379 个源码文件 typing 通过，默认测试在 12 个失败后停止：4636 通过、4 跳过、12 失败；失败分布于时间范围/日历、fold、mean、`metric.where` 等旧来源正例及依赖其 Artifact 的续算测试，均在 Run 前 `source_admission` 阻断，API 文档阶段未执行。单独 `make docs-api-agent` 与 `npm run build --prefix site` 成功（Astro 检查 0 错误、0 警告，321 页构建），`git diff --check` 通过。现场版本为 Ibis 12.0.0、DuckDB 1.5.3、SQLite 3.53.1、PyArrow 25.0.1。独立预期由固定的两行大整数、分组计数、空输入、伪造/跨会话编译句柄、提前关闭和异常清理断言提供；精确解码另以小数转整数、int64 溢出、float32 降精度、float→Decimal、嵌套 bool 值类型改变的拒绝及 Decimal 保真为反例。新增 Population 与基础 Metric 测试钉死旧 DuckDB `statement(sql)`；Population 用例还比对 Runtime 观察到的提交 SQL 与 `CompiledRead.sql`，并检查 session 关闭；远端中断只有 `remote_unknown`，未宣称已终止。`md.raw_sql` 仍走独立终端且结果不能绑定到新 adapter，已由定向用例验证。此证据仅覆盖本地 DuckDB/SQLite 基础形状；没有六后端远端服务运行、表形态矩阵或 R0.5 控制/认证等价能力结论。

## R0.3–R0.5 本次快照与核验

起点分支 `panda`，HEAD `ba79036dcd4642e609d4e9c2a76570ecf25d4542`；以下 hash 固定提交前的文档候选，与本文件一同提交后可按 Git 提交复核。五份输入在本轮开始时的 SHA-256：

| 输入 | SHA-256 |
| --- | --- |
| `2026-09-23-analysis-algebra-theory.md` | `40ce44e730a245b9a9dad50ac6fe64effa5c374960ef658ec9eaaa328f6a2e14` |
| `2026-09-24-marivo-semantic-analysis-dsl-interface-design.md` | `d31aa12ac4f4bffe571572e7824a7d2f33f83f6e8a372817905c5dd30ab5e4ad` |
| `2026-09-24-marivo-analysis-dsl-architecture-design.md` | `1c8bf9f9ccae7961b165b6ace061b721962bd634489a9e6dd95d0909e4d074b5` |
| `2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md` | `2c433063e7275e33481cf9fd33be28d32ea5236c6167d6484ee14b2239f93646` |
| `2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md` | `fd077522e35edc3450bfe6728544d5166c9002d3b8502e80ffdb4f255e382c72` |

用户随后明确要求保留 `md.raw_sql` 执行 raw SQL，作为 Marivo 分析能力之外的逃生通道。同步修订后的主计划 SHA-256 为 `e44e7f8628a3646cf9d571be25ec863ef8c0b605917315969a44f5c491f876de`，R0 计划为 `0256bb9e31be905e7d77774aee7607b455b035b08d4e9394293c3194a8d14d08`；原始输入 hash 保留作起点证据。本轮八份已跟踪文档的 `git diff --binary HEAD -- <八文件>` SHA-256 为 `9c1d52b29b5225926d1489763b19ff51bde84477b557313a207217be0b54ce78`；新 SQL 台账文件 SHA-256 为 `f281caac4696364c23873be7b36d281f35426be4e91c656a4338973f8b23565e`。本文件在这些 hash 之外，避免自引用。

静态核验：`git status --short --branch`、`git rev-parse HEAD`、`shasum -a 256 <五份输入及 SQL 台账>`、`rg -n 'raw_sql|statement\(|submit\(|\.sql\(|SELECT |WITH |SHOW |PRAGMA |CREATE |SET ' marivo/{datasource,semantic,analysis}`、`rg -n 'postprocess_sql|read_parquet|compile_event_bundle|integrity_sql' marivo`、导出/Help/消费者反查及 Markdown 相对链接检查；`git diff --check` 退出码 0。`source_health.py` 的实际路径为 `marivo/semantic/source_health.py`，扫描已覆盖。`make test TESTS='tests/test_datasource_raw_sql.py'` 在本次修订后 41 项通过，证明当前公共 raw SQL 路线的现有测试行为，不能授予新 DSL 的真实后端资格。独立反例按 owning spec/台账审阅了 DST、重叠 Anchor、25/5/70 界、权重缺层、比率缺侧与同刻顺序；这些是规则审阅，非测试执行通过。

未验证：新目标测试文件/独立 oracle 执行、真实六后端/表形态/资源、安装 wheel、真实 Agent 旅程；旧 J1–J4 或 C0–C10 成绩不转授。阻塞：R0.5 的无 SQL 控制/认证替代仍待实证，R0.6 交接未开展。后续阶段应分别记录实际代码 SHA、diff hash、owning spec 版本、通过/失败/未验证/阻塞单元及精确命令。
