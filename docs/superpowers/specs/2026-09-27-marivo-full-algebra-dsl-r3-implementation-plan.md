# R3：统一代数内核、方法注册与执行图实施文档

Date: 2026-09-27

Status: R3 execution plan；本文规划 R3，未实施或验收 R3 产品代码。

本工作包执行[全量重构实施计划 §9 R3](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md#r3--建立统一代数内核方法注册与执行图)，
以[分析代数契约 §4–§6、§9、§11–§12](2026-09-23-analysis-algebra-theory.md)、
[目标 DSL 接口设计](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md)、
[执行架构设计 §3–§5](2026-09-24-marivo-analysis-dsl-architecture-design.md)、
[R0.4 六类规则与消费者台账](2026-09-26-marivo-full-refactor-r0-capability-ledger.md#6-r04-六类元算子规则冻结)、
[R1 实施文档](2026-09-26-marivo-full-algebra-dsl-r1-implementation-plan.md)、
[R2 实施文档](2026-09-27-marivo-full-algebra-dsl-r2-implementation-plan.md)和
[阶段验收主记录](2026-09-26-marivo-full-refactor-acceptance.md)为交接依据。
方法的业务含义仍由各 owning spec 决定；本文固定内核、注册、图与纯计划的实施顺序，
不以一个通用类型代替 C03–C14、C18 的具体方法契约。

## 1. 起点、责任与成功口径

用户说明 R0/R1/R2 任务已完成。起草时 checkout 为 `panda`、HEAD `784135090b`；
工作树中已有未跟踪的 R2 实施文档，R3 不修改该文件。当前版本化验收主记录仍将
R0.6 标为未验证、R1 整体标为未通过、R2 静态交接通过而整体未通过；
C02.b 的消费侧、R1 控制/metadata/远端资格和 64 个已有 skip 等仍按原归属开放。
R3 实施前须复核实际代码和交接记录；进度说明不能自动把这些格转成阶段验收通过。
R3 可使用已交付的 typed Semantic 定义和 R1 合格的来源绑定推进纯构造及计划工作，
需要未取得资格的实际读取时保持对应格未验证或阻塞。

R3 主责是 `analysis/core`、`analysis/methods` 的共同契约与单一方法注册、
有类型 Logical 图、`analysis/compiler` 的纯准入/放置和基础 lowering。
R0 台账中六类元算子 `bind_project@v1`、`map_correspond@v1`、`cell_derive@v1`、
`row_state@v1`、`original_reduce@v1`、`parts_transport@v1` 是输入规则。
R3 建立其**签名、前提、输出、部件和执行义务**；具体成员/观察/归约、比较、Event、
Lifecycle、统计方法的完整来源执行与结果公开分别留给 R5–R8。

R3 的可核验目标是：一个由精确 Ref/规范 Metric 图或固定 Artifact 叶子构成的表达式，
经无 I/O 构造得到唯一有类型图；同一注册方法的语义规则、实现候选和未履行义务可被
纯计划解释；不合格输入在最早可确定处结构化拒绝；合格的基础来源计划只产出 Ibis
表达式或预先注册的本地方法阶段，且不自行提交、分配 Run 或发布 Artifact。
已有 J1–J4 场景身份和旧 Dataset 家族不能成为新内核类型或方法资格依据。

R4 拥有 Run、求值身份、Arrow 交换、Store、receipt、物化与冷恢复；R5–R8 拥有完整
公共方法与领域执行；R9 拥有全后端资格和遗留 SQL 清零。R3 只把这些后续消费者需要的
typed 交接固定下来。它不以预建空包、旧路径转发 shim、第二套可执行 AST 或全局优化器
表示进展。

## 2. 工作包

### R3.1 提取一套域、量、Cell、部件和规则实例

从 `analysis/datasets/{descriptors,handles}.py`、`analysis/observation/contracts.py`、
`analysis/public_dsl.py` 的 J1 节点和旧 Dataset 描述符提取可复用的内部值模型。
域记录实例身份、实际/目标坐标、角色与明确对应；量单独记录“回答什么问题”、单位、
时间与贡献定义，不把同键、同显示值或同物理 schema 当作同一量。
Cell 区分 `Defined`、`Null`、`Undefined`、`Unknown` 及原因；缺侧
`MissingCoordinate` 属配对事实，不写成 Cell Null。部件按主体映射、端点、原方法状态、
覆盖、固定参照及各自绑定登记，缺失部件同步撤销依赖它的续算能力。

将 R0.4 六规则实现为封闭的 `InputSignatures + Parameters → OutputSignature + Pre +
RequiredParts + PartTransform + Post + Transport + Eval` 推导。构造期检查 Ref kind、
session/owner、版本、路径、字段所有权、值角色、已知对应和所需部件；尚需检查的来源
键、覆盖、数值与精度进入带输入/范围绑定的义务，不伪装成已成立证据。
保留声明、Semantic builder 推导、本次检查和实际来源观察各自的依据与依赖；
`Post` 只能在子节点义务已履行的前提下供父节点消费。

成员规则独立验证：Entity 完整主键及显式版本选择形成根域，保持单射的限制不自动
`distinct`；Journey/Interval 等非单射主体映射取唯一主体集合像。来源声明只能支持
获准的推导，已观察到的重复身份必须报错，不能通过 `distinct` 掩盖。多根坐标使用实际
完整元组的并集，不将各轴值做笛卡尔积；映射的单射、单值、覆盖与重数分别登记。
当前行 `summarize` 新建 RowStatistic 状态，原量 `rollup` 合并原组件再 finish；
两者不因物理聚合形式相似而共用量定义。

**交付：**唯一内部签名/规则表示、六规则正反例、主体映射和部件运输反例；删除迁入
范围内的重复描述符判断。公开协议仅见域、单量关系和领域结果的封闭变体，不暴露证明、
传输句柄或任意 `payload` 字典。

### R3.2 归并方法语义与物理实现注册

以 `analysis/operators/{registry,dsl_j1_contracts,*_contracts}.py`、旧 Dataset registry 和
R0.4 方法版本表为迁移清单，为每个已接入的方法指定唯一语义 owner。
方法注册的语义层固定输入/参数封闭变体、量定义、Pre、Cell 政策、方法状态、
RequiredParts、K 和结构化拒绝；实现层按同一 `method@version` 声明精确类型、来源/时间
形态、后端/表形态、路线、检查义务、数值精度与资源条件。adapter 只兑现物理资格，
不得修改空贡献、业务顺序、比较缺侧、单位、权重或状态含义。

启动时拒绝重复语义 owner、重复冲突实现、未知方法/版本/类型和不完整注册；
已注册但尚未具备实现的格显式为不支持、未验证或阻塞，不以旧 J1/旧家族 registry
补路。注册表不是公开方法总表，也不推出用户可调用的半成品入口；具体公共方法随
R5–R8 及其消费者同迁。`md.raw_sql` 继续是 datasource 终端，不能注册成 Analysis
实现。一个方法可有源端 Ibis 或本次来源经 Ibis 准备后的已注册 Python 路线，但路线
在执行前决定，不因编译、读取或运行失败自动改选。

**交付：**单一语义/实现注册 owner、方法版本到资格键的精确映射、冲突/缺项/错误类型
的拒绝用例；旧 operator、MVP 和 Dataset 分支在已迁方法上没有第二个可执行注册。

### R3.3 统一有类型图、输入分类与纯计划

以 `analysis/public_dsl.py`、`session/_lazy_graph.py`、`compiler/{placement,source_admission}.py`
和旧 Dataset 节点为入口，形成一个有类型图。边标明主体、量、比较端点、参照、
来源或固定输入等角色；节点保留定义指纹、显式节点 identity、方法版本、参数、
输出签名、RequiredParts、前提和待履行义务。定义身份不等于本次 realization 或
Artifact ref；同形但独立构造的节点不能自动合并。Materialized 只作为固定叶子，
其历史 lineage 不展开成现场来源。R4 在此图上绑定一次 Run 和实际共享实现。

分类仅遍历执行根的真实数据依赖，得到纯来源、纯固定或显式混合三种输入。
纯来源可在合格的同源 Ibis 路线与预先注册的 Ibis 准备→Python 路线中计划；
纯固定只可经受控 Artifact 读取→本地方法；现场来源与显式 Materialized 混合在
Run 和任何来源/Artifact 读取前拒绝。不同 datasource 的现场组合不因单个 adapter
合格而获得联邦资格。图与计划不打开连接、不调用 SourceSession 读取、不扫描 Artifact、
不分配 Run；来源 schema 的即时验证也不能伪装成纯计划。

准入按已知静态事实、带范围的可信声明、可履行运行检查分层处理。缺少主体映射或
方法状态等不可恢复部件时即刻拒绝；可履行的实际键集合、覆盖、数值条件保留为计划
义务，执行 owner 必须在依赖其结果或发布前完成。计划产出有类型阶段与单一已选实现，
拒绝原因明确 expected、received 和可行 repair；静态实现声明与本次物理事实分开。

**交付：**一套图和纯计划 API、固定叶子及混合早拒绝测试、无来源 I/O/Run/Store 的
构造反例，以及给 R4 的阶段/义务交接类型。旧路径若尚未迁入，不得被新图调用作为
fallback；删除时机与 R0.4 消费者台账对齐。

### R3.4 基础 lowering、规则变换与阶段披露

compiler 只把**已准入**的基础绑定、投影、筛选、对应、组件状态与必要校验降为
Ibis 表达式，或产出已注册本地方法阶段。来源表达式交 R1 `SourceSession` 编译与传输，
R3 不输出可直接提交的 SQL 字符串，不使用 `backend.sql`、手写 SQL/CTE、生成后补丁，
也不在共享 compiler 中按 backend 名称分支。表达式不支持时记录精确资格缺口；
不以 pandas 任意函数或 Artifact→DuckDB 补救。

对 L1、L7、L8、L9 只实现有注册前提的局部变换：分别比较数值/状态、量定义与 K、
输入绑定、部件/依据运输及拒绝行为。L8 的状态相等不能自动许可 RowStatistic 冒充
原量 rollup；L9 的组状态相等不能自动许可来源下推或省略显式空组。
浮点容差仅用于数值 oracle，不判断身份或量定义。变换正例须有独立固定输入结果，
反例覆盖 Unknown 谓词、非单射映射、缺组件、空目标组和来源范围变化。

同步更新已改变的 Analysis owning spec、内部契约说明、受影响公共 docstring/类型、
原生 Help、结构化错误、`__all__` 快照及独立 drift/reachability/budget 测试；
若本阶段确实改变公共 API，再同步 `site/` latest 英中示例与 CLI。
已接入的公开终端结果遵守有界 `repr`/`show()`、按当前部件与状态给出真实
`.contract()`；未实施的 R5–R8 续算不在 Help 或动态 K 中暗示可用。
如确需编辑 packaged `marivo-semantic`/`marivo-analysis` skill，先遵守仓库
`AGENTS.md` 要求取得明确批准；本文不编辑 skill。

**交付：**Ibis/本地阶段的 typed 降低、局部规律反例、与 R4/R5 的消费者交接、
公共披露及更新后的验收主记录。每格记录通过、失败、未验证或阻塞，附代码 SHA、
输入、独立预期、后端/形态、命令与恢复条件；历史 skip 保留原断言和归属。

## 3. 核验与阶段出口

先用纯构造和注册单测验证六类规则、四种 Cell、主体集合像、部件裁剪、重复注册、
固定/混合分类及零 I/O；再以 R1 已具资格的基础来源做 Ibis 表达式与实际提交边界
的定向集成。可从 `tests/test_analysis_dsl_j1_construction.py`、
`test_lazy_predicate_algebra.py`、`test_lazy_source_algebra.py`、
`test_lazy_local_placement.py`、`test_lazy_contract_source_admission.py`、
`test_lazy_dataset_registry.py` 和 `test_analysis_dsl_j1_source.py` 定位现有反例；
旧测试名仅供定位，不能直接证明新图。修改共享 fixture 时使用
`marivo-test-fixtures` skill。规则 oracle 应来自原始键/行、纸面状态与独立结果，
不能由候选 reducer 反算自身预期。

使用 `make test TESTS='...'`、`make runtime-test TESTS='...'`、
`make typecheck TYPECHECK_TARGETS='...'` 和 `make lint-agent LINT_TARGETS='...'`
逐步核验；共享行为及公共契约收口运行 `make check-agent`，API 文档和站点构建按主计划
§12.1 执行。普通 R3 不运行完整 `make release-check`。

R3 只有同时满足以下条件才标为通过：

1. 六类规则在唯一内核中推导有类型签名、前提、部件变化、依据及未履行义务；
   Entity/Subjects、当前行/原状态、Cell/缺侧和量/域分离均有独立正反例。
2. 每个已接入操作只有一个语义方法 owner，物理资格按方法版本与后端/形态分别判定；
   重复、未知和不合格路线拒绝，运行失败不会触发另一路线。
3. 唯一图的构造与纯计划没有来源或 Artifact I/O、Run/Store 操作；来源、固定和混合
   依赖分类准确，混合在读取前拒绝，显式节点/定义/求值/Artifact 身份互不混同。
4. 已接入来源 lowering 只构造 Ibis 表达式并经合格 adapter 提交；L1/L7/L8/L9
   只在本方法前提与部件/K 保持成立时启用，局部反例不出现语义或执行越权。
5. 相关测试、typing、lint、API/Help/文档门禁通过，R0/R1/R2 开放格与 R5–R9
   的领域实现、后端资格和 skip 按验收主记录保持真实状态。

R3 出口向 R4 交付有类型图、纯计划、阶段和运行检查义务；向 R5–R8 交付唯一规则/
方法注册及可扩展的具体方法接入位置。R3 通过不代表 Runtime/Store 协议、
六后端完整方法矩阵、Artifact 冷恢复、安装包或真实 Agent 已验收。
