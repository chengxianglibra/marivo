# Marivo Analysis DSL S1 双执行基础实施计划

Date: 2026-09-24

Status: W1–W4 私有接缝已实施；W4 运行内多消费者共享尚无 J1 图形可验，[S0 验收](2026-09-24-marivo-analysis-dsl-s0-acceptance.md)保留当时快照，W5 与 S1 总验收尚未完成。

## 目标与边界

本计划落实 [MVP 验证计划](../specs/2026-09-24-marivo-analysis-dsl-mvp-validation-plan.md#6-实施阶段与完成标准)的 S1：用同一有限方法契约接通真实 DuckDB 来源的 Ibis 路线和受控 Artifact 输入的 pandas 路线，完成 J1、基础状态归约、交换/发布/冷恢复及新执行身份协议。S1 验收只覆盖实际接入的方法、形状和后端，不把整个目标 DSL、J2–J4 或其他后端记为通过。

输入契约以已接受、公开仍未激活的 [Analysis 切片](../../specs/analysis/python-analysis-design.md#accepted-s0-analysis-dsl-slice-inactive)、[方法规则](../../specs/analysis/operators-and-frames.md#accepted-s0-method-rules-inactive)和 [Runtime 协议](../../specs/analysis/session-state-and-runtime.md#accepted-s0-input-and-execution-protocol-inactive)为准；公开目标语法以[接口设计](../specs/2026-09-24-marivo-semantic-analysis-dsl-interface-design.md)为准，放置、交换和恢复义务以[架构设计](../specs/2026-09-24-marivo-analysis-dsl-architecture-design.md)为准。S0 留下的 A01、A06–A09、A12 是本阶段的主要生产接缝；[S0 独立 fixture](../../../tests/test_analysis_dsl_fixtures.py)只提供 oracle，不算 J1 的 DSL 执行证据。

J1 的可观察目标是：固定夹具的总收入 1000，按地区 east=600、south=400、west=Null，选出 east 成员后按订单渠道 web=450、mobile=150；无订单客户不产生渠道值。`customers.group_by(Region)` 与同绑定 `read(Region)` 后分组一致；`customers.group_by(Channel)` 因非单值属性拒绝。三次顶层执行分别读取当时的来源，不承诺跨次共同快照。

S1 采用现有 Session、Dataset、registry、BatchStream、Store v6 和受控 Parquet 读取接缝，不建第二套 AST/Runtime/Store，不迁移旧 Artifact，不让未迁入方法的行为被新协议全局改写。J1 可先通过受控的内部调用链验证；若激活公开签名，则须先在 owning specs 接受确切形状并同时更新 Help、导出/类型、示例和 site latest 中英文文档。私有链通过不得写成公开 DSL 已交付。packaged skills 仍按 AGENTS.md 的单独授权规则处理，本计划不安排编辑。

## 基线与实施顺序

规划时 `panda` 分支 `HEAD` 为 `a4757f2b31f9b34a2d67510dbc7576bfadf4f5ea`；S0 验收记录已加入暂存区。实施前记录新的代码/文档 SHA、依赖版本和工作区状态，保留现有工作，不覆盖暂存内容。若使用隔离 worktree，确认本计划与 S0 记录均已带入；`docs/superpowers/plans/*` 默认被 Git 忽略，不能因新 worktree 缺文件而改用旧输入。

按 **W1 → W2/W3 → W4 → W5** 推进。W2 与 W3 依赖 W1 的真实声明和绑定，可分别实现；W4 只在真实路线、codec 和必要检查都接好后形成成功发布。每个工作包先用能区分错误语义的独立反例验证，再实现最小接缝。

### W1. 关闭 J1 的真实声明和有限构造链

**主要 owner：**`marivo/semantic/_authoring_declarations.py`、`_authoring_validation.py`、`metric_graph.py`、`metric_graph_lowering.py`；`marivo/analysis/datasets/`、`observation/`、`operators/registry.py`。

补齐 J1 实际消费的 Metric 单位、可加性、事件时间角色、Null/空贡献和值政策声明及版本化绑定；从正式 authoring、加载器与规范图取得依据，不从装饰器 body 推断，也不在 fixture 手贴可合并标记。受信 Entity 声明不增加隐藏的来源唯一性预检；来源列类型仍在执行时观察。缺声明、已知矛盾或无法履行政策时，在最早可确定处拒绝。

接通 J1 所需的成员、分类 read、严格 where→members、单值 Region 分组、Revenue 观察、贡献 Channel 坐标及原状态 rollup 的有限 Logical 图。成员域与贡献坐标、定义身份与显式节点身份、观察的实际来源绑定分开；构造、字段句柄和纯计划零业务 I/O。同一显式节点在一次求值中共享实现，同形独立节点不自动合并。仅注册有真实实现和证据的方法/类型/路线；未实现的目标语言继续拒绝。

**出口：**真实 J1 声明经正式加载；构造和拒绝测试证明不猜路径、不把 Channel 当客户属性、不在构造期读取来源。缺 A01 权威元数据时停止对应方法准入，而非用测试替身绕过。

W1 实施记录（2026-09-24）：Measure/Metric authoring 已切换为封闭可加性构造器；
`ms.aggregate(time=...)` 与 decorator 的时间、单位和值政策进入正式加载和规范图，
版本化政策进入依赖与私有 Logical 定义指纹。J1 私有链完成成员、Region read 与
`where → members`、同绑定分组、Revenue 观察、Channel 贡献坐标和原状态 rollup；
方法规则没有来源或 pandas 实现资格。独立 SQL oracle 的 west 收入为 Null，
它只证明夹具预期，不构成 DSL 数值执行验收。

### W2. 实现同契约的 Ibis 来源与 pandas 本地计算

**主要 owner：**`marivo/analysis/compiler/placement.py`、`lowering.py` 及 J1 对应的来源编译 owner；`marivo/analysis/materialization/source_stage.py`、`local_stage.py`；实际方法的 `operators/` owner。

在同一方法/版本规则下实现 J1 需要的绑定、单值映射、筛选、sum/count 状态、分组及原状态合并；同时接通当前行 sum/count/mean 的基础 RowStatistic 状态，明确其统计单位和空组政策，不能复用原 Metric 的贡献状态。来源适配只构造 Ibis 表达式，产品分析和校验 SQL 交给 Ibis 编译；不拼接分析 SQL、注入原始 SQL 或补丁修正生成结果。J1 的真实 DuckDB 读取按已注册能力准入，来源执行失败不触发 pandas 兜底。

固定 Artifact 输入经 receipt 校验后转 pandas，在本地执行已准入的筛选、分组与状态归约；不得通过 `ParquetBinding`、DuckDB connect/register/scan、临时表或上传重进来源引擎。来源与本地适配各自处理严格 Cell 消费、显式键、有效空贡献、int64 溢出、非有限值与 float64 输出，政策本身只由方法 owner 定义。针对真正具有双实现的原子能力，用同一独立向量核对数值、域、Cell、部件和拒绝含义；物理查询形状可以不同。

**出口：**J1 的来源结果与独立 oracle 一致；保留输入上的合法续算与来源路线语义一致，断源且禁止 DuckDB 时仍可执行；不支持的类型/后端/方法在数据读取前给出明确拒绝。

### W3. 贯通真实双生产者、受控 codec 和 K

**主要 owner：**`marivo/analysis/materialization/execution.py`、`ibis_batches.py`、`reads.py`、`storage.py`、`contracts.py`、`publication.py`、`dataset_publication.py` 及相关 codecs。

将真实 DuckDB/Ibis 来源 reader 与 receipt 校验后的 Parquet reader 接入 S0 的同一固定 schema、`ValidatedExchangeStream` 和 `BatchStream.close()` 边界。创建流即可读 schema；零批次保留空 schema/空批次；每批字段、顺序、类型和 Cell 标签一致。拥有 reader/游标/文件的适配器在耗尽、提前停止、迭代错误和取消时释放资源；未耗尽的行数、摘要、覆盖检查不得签成已完成，也不得发布成功 Artifact。

用现有主表、私有部件和 Store receipt 发布 J1 的实际域、量/方法版本、Cell、sum/count 状态与输入绑定；本地输出同样经过固定 schema 与完整性检查。Artifact→受控 Parquet→pandas 以及新进程冷恢复要保持已承诺的 K，至少覆盖保存的 Entity 行关系合法筛选/成员提取、当前行统计，以及保存的完整收入观察按已有坐标 rollup。不得从展示值重建丢失状态、从来源补齐未保存属性，或把解码成功当作已完成检查。沿用 S0 的四 Cell、nullable int64、时区/精度、乱序键、部件换序和损坏 receipt 向量，再由真实两类生产者各跑一遍；Decimal 只验交换精度，不准入首轮数值方法。

**出口：**V06 的真实生产者与完整发布链通过；断源冷恢复后合法续算的结果与恢复前一致，所有失败路径保持原子发布和资源关闭。

W3 实施记录（2026-09-25）：以 `panda` 干净代码 SHA
`a749125c2e87af8982a8c7d9effc628149221c3d` 为基线；实施代码 SHA
`650da09a4737d555ac67fef79c5fce9f226719bf`。验证依赖为 Ibis
12.0.0、pandas 2.3.3、PyArrow 25.0.1、DuckDB 1.5.3。

- 私有 J1 关系/Cell 流经过固定 schema 和 `ValidatedExchangeStream`；真实 Ibis
  reader 的已声明结果 schema 在创建 reader 前确定，原始批次类型漂移、错序键、非法
  Cell 和未耗尽流拒绝完成证据。受控 Parquet receipt 和独立坐标部件在完整读取、
  哈希、行数、键及状态检查后进入 pandas。
- 已准入 Run 经现有 Store 发布版本化 J1 descriptor、主表和 sum/count/坐标
  receipt；按精确 Artifact 引用在新进程断源并禁止 DuckDB 连接后恢复，Channel
  合法续算得 `web=850`、`mobile=150`，与来源结果一致。本地派生分组和当前行
  count 也可发布和恢复；损坏坐标 receipt 与提交前故障均拒绝成功 Artifact。
- W2 审查建议已甄别并修复：畸形 summarize root 改为结构化拒绝；float64
  跨路线不承诺逐位相等，坐标分区采用有界比较；本地 int64→float64 非精确转换
  拒绝；额外修复 Arrow `double` 在本地方法准入时的类型归一化。补充非字符串
  坐标、多个成员合并、时区 offset 和资源关闭向量。
- `make test TESTS='tests/test_analysis_dsl_j1_source.py tests/test_analysis_dsl_j1_construction.py tests/test_analysis_dsl_exchange.py tests/test_analysis_dsl_j1_artifact.py tests/test_semantic_live_help.py tests/test_unified_help.py tests/test_agent_api_drift.py'`：129 passed。
- `make runtime-test TESTS='tests/test_analysis_dsl_j1_artifact.py'`：1 passed。
  触及模块的定向 `make typecheck TYPECHECK_TARGETS='...'` 和
  `make lint-agent LINT_TARGETS='...'` 均通过；`make check-agent`：5100 passed、
  4 skipped，lint、typecheck、导入约束和 API 文档构建通过；正常 commit hooks 全部
  通过。迭代中发现并修复了 Ibis 原始聚合批次以 Decimal 返回、而 Ibis 声明
  int64 的差异，最终完整门禁重新通过。

本记录是 W3 私有链结果，不构成 W4 执行身份、缓存命中或 S1 总验收。

### W4. 接通 Runtime 执行身份、缓存与失败协调

**主要 owner：**`marivo/analysis/materialization/dataset_execution.py`、`execution_key.py`、`store.py`、`dataset_publication.py`、`reconciliation.py`；`compiler/normalize.py` 的已接受分类函数。

新切片先纯检查并按本次传递依赖区分来源、固定 Artifact 和混合输入；简单混合图在 Run 准入、来源连接和 Artifact 行读取前拒绝，更复杂的 compare/新观察组合留待 S2。取得现有 Session writer guard 并协调未完成 Run 后，固定输入用确切 receipt/方法/协议版本 key 查找，完整校验命中且不创建 Run；来源图不按定义查历史。来源 miss 在准入边界分配 Run ref，用 S0 的 `source_execution_key` 绑定本次 key，再以同一 ref/key 完成 `SessionStore.admit`、descriptor、receipt、发布和精确恢复。不得把随机量写进定义指纹，也不得按“最新结果”重定向旧引用。

集成测试必须对**同一个 Lazy Python 对象**先后执行，期间修改受控来源：定义指纹不变，两次 Run/key/Artifact 不同，两个旧新结果均可在断源新进程中按精确引用恢复。另令第二次执行在数据准入或发布前失败，确认首次 Artifact 不变；模拟提交回执丢失、未完成 Run、固定输入竞争命中，检查原 Run/receipt 的 reconcile/read-back，不自动重放来源。固定输入重复执行复用同一 Artifact，Run 数不增加；改变 receipt 或方法/协议版本不得误命中。运行内共享与跨次新求值分别计数。

**出口：**V08/V10 在 S1 已接入形状上的重复求值、精确命中、失败保护与恢复成立；旧生产路径的执行协议未被误改。

W4 实施记录（2026-09-25）：以 `panda` 代码 SHA
`1a839129cebad78b9c53e997c02844c7a619a35c` 的干净工作区为基线；
本阶段的实施 SHA 由本记录所在提交标识，S1 总验收另行记录。

- 私有 `DatasetRuntime.execute_j1(...)` 在 Session writer guard 下使用现有
  Store v6、来源/固定 v2 key、W2 Ibis 与 W3 pandas/receipt 生产者。来源调用
  在准入后打开 source factory；固定输入先匹配精确 Artifact 和所有 receipt，
  命中时完成内容校验且不新增 Run。混合的固定成员加现场 read 在 Run、来源和
  Artifact 行读取前拒绝；旧 `DatasetRuntime.execute()` 仍使用 v1 协议。
- 同一 J1 节点两次顶层调用之间修改受控 DuckDB 来源：定义指纹稳定，两个
  Run/key/Artifact 各不相同，A 成员收入从 450 变为 500；断源新进程按两份
  精确引用恢复。固定 Channel 续算重复命中同一 Artifact，换用第二份来源
  Artifact 则得到不同 key/结果；两个 Runtime 竞争同一固定输入时，忙碌者
  明确拒绝，重试后精确命中。一次调用只打开一次来源，固定续算不打开来源。
- 来源打开失败、提交前故障和提交回执丢失分别验证原 Artifact 保持不变、
  原 Run 终结或精确回读；待协调 Run 按原 ref 失败后才准入明确发起的新调用。
  冷恢复回归还修复了追加已校验部件列时丢失 Arrow 非空字段属性的问题，
  并断言恢复前后完整 schema 相等。独立 float64 输入回归覆盖本地 sum/mean
  的 Arrow `double` 类型归一化。
- W4 编排暂归私有 `dsl_j1_runtime.py`，Store 只统一 Run ref 分配格式，
  `admission.py` 保留入口与逐次诊断；没有把 J1 路线铺进现有 v1
  `dataset_execution.py`。这与上列主要 owner 路由不同，目的是保持旧路径
  的执行协议不变。损坏固定 receipt、方法版本和 key 协议版本均验证不会
  命中旧 key；失败对账异常不再被吞掉。逐次诊断分别计数来源求值与固定
  缓存命中；同一节点的两次顶层调用各自求值一次。当前获准的 J1 图均为
  单前驱，无法形成一次执行内一个显式节点被多个消费者使用的有效图，
  因而“运行内共享”验收仍未验证，不能由来源打开次数或缓存命中替代。
- `make test TESTS='tests/test_analysis_dsl_execution_identity.py tests/test_analysis_dsl_j1_construction.py tests/test_analysis_dsl_j1_source.py tests/test_analysis_dsl_exchange.py'`：49 passed；
  `make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py tests/test_analysis_dsl_j1_artifact.py'`：7 passed；
  默认 typecheck 与 lint 通过；`make check-agent`：5101 passed、4 skipped，
  lint、typecheck、导入约束和 API 文档构建通过。直接对新增测试模块运行
  mypy 仅剩 `tests/shared_fixtures.py` 的六项既有类型错误；S1 总验收仍由 W5 记录。

### W5. 记录 S1 验收和 S2 交接

使用 S0 的真实声明与独立 J1 oracle，在 `docs/superpowers/plans/2026-09-24-marivo-analysis-dsl-composition-acceptance.md` 记录代码 SHA、依赖/非敏感配置、fixture 版本、命令、实际结果、失败复现及证据位置。分列文档/类型、Ibis 编译、真实 DuckDB 执行、pandas、交换/冷恢复、Runtime/原子性；每项只写通过、失败、未验证或阻塞。记录 S1 实际新增/修改的语义内核和适配位置，以供 S2 比较扩展代价。其他后端、J2–J4、完整 V01–V15、公开 Help 和真实 Agent 保持未验证。

测试文件名在实施时按 owner 确定；优先复用 `tests/test_analysis_dsl_fixtures.py`、`test_analysis_dsl_contracts.py`、`test_analysis_dsl_exchange.py`、`test_analysis_dsl_execution_identity.py`，为真实 J1/双路线与 Runtime 冷恢复新增少量集成用例。先运行修改范围的 `make test TESTS='...'`、`make runtime-test TESTS='...'`、`make typecheck TYPECHECK_TARGETS='...'` 和 `make lint-agent LINT_TARGETS='...'`，最终运行 `make check-agent`；文档-only 本计划不以未运行测试冒充实施证据。不启动远端服务或 MinIO，不运行普通开发不需要的完整 release-check。

S1 只有在 J1 数值/域/状态、双路线一致性、真实两类流和完整发布、纯本地断源续算、重复来源执行与固定命中、失败原子性及关闭边界都留下可复现证据后才关闭。S2 接手 compare、J3 多根组件、更多混合拒绝和共享成员绑定的双 Artifact 契约夹具；S3 接 Spearman、完整矩阵边界与成本。S1 的局部通过不能替代这些阶段。
