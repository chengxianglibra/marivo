# R1：统一 Datasource Adapter 与基础 Ibis 读取实施文档

Historical execution records, qualification inventories and one-time validation
scripts were removed during the 2026-10-06 cleanup. Committed records remain in
Git history; local-only execution files were discarded. Recorded phase results
below describe their original scope.

Date: 2026-09-26

Status: R1 execution plan；R1.1–R1.5 已部分实施，整体验收仍未通过；当前证据见阶段验收主记录。

本工作包执行[全量重构实施计划 §9 R1](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md#r1--统一-datasource-adapters-与基础-ibis-读取)。
R1 接收 R0 的[C01 能力去向](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#2-c01c18-能力去向)、
[SQL/adapter 台账](2026-09-26-marivo-full-refactor-r0-sql-ledger.md)和
[Datasource owning spec](../../specs/semantic/datasource-layer.md#ibis-owned-analysis-reads-and-terminal-raw-sql)，
交付一套来源连接、物理绑定、Ibis 编译提交、批次解码和资源管理的内部责任接口。
它保留 `md.raw_sql` 的公共终端用途，不授予该结果 Semantic 或 Analysis 资格。

## 1. 开工依据、范围与成功口径

用户说明 R0 已完成。起草前 checkout 为 `panda`、HEAD `1bd9b7e98a3c9f7878de73e07f89aeabdc740073`，
工作树无改动；版本化阶段验收主记录 (historical record in Git history)仍写有
R0.6 未开展及 R0.5 控制/认证替代可行性未证。执行 R1 前须核对 R0 的最终交接和实际代码
基线，将验收主记录与已完成事实对齐；不能因本实施文档或用户的进度说明，把未运行的后端格
改成通过。若交接中仍有独立阻塞单元，R1 先推进不依赖它的工作，并在同一主记录留下精确格子。

R0.6 于 2026-09-27 补交[breaking change 与 R1/R2 交接](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#7-r06-breaking-changes)。
其中 B13 的 [Datasource owning spec 目标](../../specs/semantic/datasource-layer.md#public-connection-boundary)
要求在 R1 公共切换时删除返回原生 backend 的公开 `md.connect` 和对应 Help target，迁移
消费者后使 `md.raw_sql` 成为唯一公共原始 SQL 终端。R1.5 已同时移除
`DatasourceCatalog.connect`、公开 `DatasourceConnection` 与相应 Help；原起草快照与
R1.1–R1.5 的实际通过/阻塞仍以主验收记录为准。

R1 主责是 C01.a/b/c 及其 C16 公共披露：六种 typed datasource spec 的连接、表和文件/JSON
来源、作用域参数、inspect/sample/test/preview/source-health 的物理读取、秘密边界与唯一终端
`md.raw_sql`。基础 Analysis 来源链只接入成员所需的扫描、过滤、投影、分组和必要校验设施；
成员/版本的业务规则由 R2/R5 拥有，统一图与 Runtime 分别由 R3/R4 拥有。R1 不据此宣称
C03–C18 的方法或固定 Artifact 续算已通过。

R1 的可核验出口为：六个 adapter 用同一内部契约完成各自基础正反例；每个已接入的受治理
读取可追溯到绑定的 Ibis 表达式及原样提交的编译产物；来源型业务校验没有手写 SQL；
未选择的可选驱动不会妨碍核心导入；取消、提前关闭、空 schema 和精确解码有实际证据；
`md.raw_sql` 仍是唯一公开原始 SQL 终端入口。真实环境缺失或必需控制能力无合格实现时，
相应单元为未验证或阻塞，不以旧 C0–C10、mock、SQL 生成或静态扫描替代。

## 2. 工作包

### R1.1 归并 adapter 契约与执行所有权

以 `marivo/datasource/engines/base.py:EngineProfile` 和
`marivo/analysis/materialization/execution.py:ExecutionAdapter` 为当前职责来源，按主计划 §5.1
及 SQL 台账 §3 的七项责任形成**一套内部接口**：provider/连接、物理来源和 metadata、
实现资格、Ibis 编译和传输、结果解码、资源/取消、来源覆盖事实。接口只传 typed 来源、
表达式、参数、用途、预期 schema 和绑定身份；受治理入口不得接受任意 SQL 字符串。
方法语义仍由 Analysis method owner 判定，adapter 只声明和兑现物理资格。

迁移 resolver、连接生命周期及实际消费者时，同步删除被替代的 profile/执行方法和
`statement(sql)`、`postprocess_sql` 等通用文本入口；不留转发 shim 或第二份资格矩阵。
原生驱动 reader 可以保留，但只提交与表达式身份、用途和 schema 绑定、未经改写的 Ibis
编译产物。`BatchStream` 维持首批前固定 schema、空流 schema、close/interrupt 及部分消费
行为；R4 的 receipt、Store 和 Artifact 协议仍由 R4 改造。

本包同时盘点依赖这个旧文本接口的 Analysis 消费者。基础读取和校验的最小闭合消费者随 R1
迁移；领域 SQL 方法按 SQL 台账 AN01–AN14、AN31 留到对应 R5–R9，但不得通过新 adapter
继续提交文本，也不得成为新执行图的失败回退。无法在 R1 同步迁入的旧执行路线，记录其
阻断与删除阶段，而不是保留一个可被新链调用的兼容口。

**交付：**内部契约、六 provider 注册、单一资格判断 owner、已迁消费者及删除清单；
伪造编译产物、无合格路线和未选驱动导入均有拒绝测试。

### R1.2 六后端来源绑定、读取与物理事实

分别实现 DuckDB、PostgreSQL、MySQL、SQLite、Trino、ClickHouse 的连接、必要列/schema、
typed `TableSource` 与适用的 CSV/Parquet/JSON/HTTP 绑定、作用域参数、Ibis 编译提交、
Arrow 批次与精确类型解码。先覆盖基础扫描、过滤、投影、分组、count/完整性检查所需的
表达式；空输入、重复复合键、Null、越界整数、非有限 float、Decimal、时区/精度漂移
各以适用后端的真实输入拒绝或精确保留。不能把 `distinct` 当作重复身份修复，也不能把
物理 schema 推断成业务覆盖或新方法资格。

`md.inspect`、`SourceInspection.sample`、`md.test`、catalog preview、snapshot 与
`semantic/source_health.py` 共用同一来源读取所有者，但保持各自事实范围：metadata、
有界样本、连接探测和受治理业务值检查分别披露。DS01、DS03–DS10、DS17–DS18、DS21，
以及 AN16–AN17、AN19–AN29、AN32 中属于基础 schema/读取/类型校验的部分，在此包
逐项替换为 Ibis 表达式或公开 metadata API。物理分区值若读取业务行，必须走 Ibis；
只读账号拿不到可选 metadata 时可报告 unavailable，方法准入必需的事实缺失则拒绝。

六后端各自至少取得连接失败/权限、普通表或 view、实际来源读取、空结果 schema、
提前关闭/异常清理、精确解码和来源范围的正反例。额外形态按 R0.5 矩阵展开：DuckDB
文件/HTTP-JSON，PostgreSQL namespace，MySQL 无效日期/Decimal，SQLite 实际存储型，
Trino Iceberg 与 non-Iceberg，ClickHouse MergeTree 与 Distributed。一个形态的成功
不转授另一形态；远端取消只在已确认终止时记为终止。

**交付：**C01.a/b 的六后端基础证据、按后端/表形态/类型拆开的资格记录，及 inspection、
preview、source-health 同一 owner 的消费者迁移。扩展方法和完整后端方法矩阵留给 R9。

**后续实施项（R9 后端资格）：**R1.2 的单列主键 Ibis 标量降级仅用于 MySQL 基础
Population/sum/count；DuckDB、PostgreSQL、SQLite、Trino、ClickHouse 当前仍使用有序
`ibis.struct`。如需对这些后端启用单列标量，逐后端补齐原样编译/提交对照、批次读取后
Arrow 身份结构重建、空流和类型精度、重复/Null 身份及完整复合键校验、异常资源清理、
静态披露与真实表/view 正反例。MySQL 的单列资格不转授其他后端；MySQL 多列主键仍
在 Run 前阻断，待独立编译路径和复合键证据具备后再评估。

### R1.3 收紧 SQL、控制与凭据边界

按 R0.5 的 DS/AN 行逐点处理连接和读取旁路。`md.test` 与 source-health 的 `SELECT 1`
改为 Ibis literal 或合格 driver ping；metadata 和数据校验改用对应 API/Ibis；不再执行
provenance SQL 来计算 parity。删除 `ms.from_sql(...)` 属性/入口；历史 SQL 的业务说明放在
`ai_context`，没有专用 SQL 字段。删除为执行改写 provenance
文本的路径。DS11–DS16、AN15、AN30 的 timeout、query-only、时区、HTTP secret、
force-download、事务/会话控制必须取得实际公开 API 与运行证据；找不到等价实现时，精确
来源或后端格标阻塞，内部 SQL 例外仍为空。保留 Store owner 的 SQLite 事务白名单。

`md.raw_sql`、`RawSqlResult`、`datasource.raw_sql` Help target 和公共导出继续存在：
输入 SQL 不做解析或语句类别判定；必填理由、正数返回行界、可执行超时、显式截断/成本披露。
只读性由连接和后端权限尽力控制，无法保证只读本身不阻断；返回行或
`to_pandas()` 副本不能输入 Semantic/Analysis、形成 Artifact 或获得续算。它使用隔离的
终端提交路径，不调用受治理 adapter 的编译产物入口来伪装表达式，也不能为失败的
Analysis 后端格兜底。测试须包括实际后端权限观察、超时、截断、错误 repair、秘密脱敏和
typed reentry 拒绝。

**交付：**DS01–DS21 与 R1 所属 AN 行的实际处置和未闭合格；源码扫描、受控提交记录
与负例共同证明唯一终端边界。新内部 SQL 例外须另有用户针对具体操作、后端和用途的
明确允许，本计划不预先批准。

### R1.4 消费者、披露和阶段验收收口

同步更新受影响的 `docs/specs/semantic/datasource-layer.md`、必要的 semantic/analysis
owning spec 交接、原生 Help、公共 docstring、错误/repair、CLI 和 `site/` latest 中英文
示例。公开导出或 Help 变化同时更新 reachability、drift、预算与 `__all__` 快照；
新的公共函数使用具体类型并给出用途、参数、返回、示例及限制。若 packaged skills 的
内容确需改动，遵守仓库 `AGENTS.md` 的编辑前明确批准要求；本实施文档不编辑 skills。

验收主记录按 C01.a/b/c、后端/表形态/类型、DS/AN 台账 ID 分别写明通过、失败、
未验证、阻塞，附代码 SHA/diff hash、输入和独立预期、驱动与服务版本、执行命令、
Ibis 表达身份与实际提交来源、资源关闭结果及可取得日志。R1 交给 R2/R3/R4 的是稳定
typed adapter 契约、已获基础来源资格、尚未迁入的方法消费者和精确阻塞格；不能写
“六后端支持”而不列形态及失败项。

**交付：**更新后的验收主记录、公共披露、文档和无旧文本入口的静态/运行证据。

## 3. 核验与出口

实施时先跑最窄的 datasource、adapter、source-health、raw SQL 和 parity 测试，按变更
补充六后端真实集成与 architecture tests，再运行相关 Runtime 定向用例、typing、lint。
修改 shared fixture 或测试框架时使用仓库 `marivo-test-fixtures` skill。现有测试可从
`tests/test_datasource_typed_specs.py`、`test_datasource_metadata.py`、
`test_datasource_json_source.py`、`test_datasource_raw_sql.py`、
`test_semantic_source_health.py`、`test_semantic_parity.py` 及各
`test_lazy_*_execution_adapter.py` 定位；测试名仅是入口线索，旧断言不能直接证明新契约。
新目标测试应让六 provider、编译产物不可伪造、未选依赖隔离、空流/早关闭/取消和终端
SQL 不可重入成为独立反例。使用仓库 `make test TESTS='...'`、
`make runtime-test TESTS='...'`、`make typecheck TYPECHECK_TARGETS='...'`、
`make lint-agent LINT_TARGETS='...'`；阶段共享行为收口运行 `make check-agent`，
API 文档门禁和 `site/` 构建按主计划 §12.1 执行。普通 R1 不运行完整 release-check。

2026-09-27 提交阶段例外：用户明确要求暂时跳过 14 个依赖 R5 旧来源方法迁移的
正例参数组合，以便提交 R1.2。每个测试保留原断言并注明恢复条件；R5 完成相应来源
方法后必须移除 skip、复跑独立预期与全量门禁。暂跳过仅解除提交阻塞，不计入下列
R1 阶段验收；具体测试和首次失败证据见阶段验收记录。

R1 只有同时满足以下条件才标为通过：

1. 六个 adapter 均按同一接口完成基础真实正反例；未选可选驱动隔离；资格判定只有一个 owner。
2. 所有 R1 已接入的受治理业务读取、count/类型/时间校验均有 Ibis 构造和原样 driver
   提交证据；共用 `statement(sql)`/SQL 后处理不能被新执行链调用。
3. 来源、metadata、样本、连接探测与 source-health 的事实范围和资源生命周期得到验证；
   schema、覆盖、主体身份与业务完整性不相互冒充。
4. `md.raw_sql` 的连接/后端只读权限观察、超时、截断、脱敏、结构化错误及不可重入边界通过；provenance
   文本不执行；内部 SQL 例外与阻塞逐行可查。
5. 相关测试、typing、lint、公共披露和文档门禁通过；未完成的 R2–R9 方法资格保持
   未验证或阻塞，不通过旧路径、fallback、批量 xfail 或目标降级填平。

R1 不改变 R4 的 Store/Artifact 协议，不实现 R5–R8 的领域方法，也不替 R9 宣称完整
方法 × 类型 × 时间/来源 × 后端 × 路线矩阵。阶段通过仅证明本页列出的基础来源边界。
