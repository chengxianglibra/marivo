# R2：统一 Semantic 业务定义和计算图实施文档

Date: 2026-09-27

Status: R2 execution plan；本文只规划 R2，产品实现和 R2 验收尚未开始。

本工作包执行[全量重构实施计划 §9 R2](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md#r2--统一-semantic-业务定义和计算图)，
以 [Semantic object model](../../specs/semantic/semantic-object-model.md)、
[loading/validation/introspection](../../specs/semantic/loading-validation-introspection.md)、
[目标接口设计 §3](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md#3-语义层应该怎样定义)、
[R0 能力台账](2026-09-26-marivo-full-refactor-r0-capability-ledger.md)和
[阶段验收主记录](2026-09-26-marivo-full-refactor-acceptance.md)为交接依据。
R2 交付可由 Analysis 消费的规范业务定义、Ref 与内在计算图；一次分析的成员域、坐标、
执行准入、来源读取和 Artifact 证据仍由后续 owner 决定。

## 1. 起点、范围与成功口径

用户说明 R0/R1 任务已完成。起草时 checkout 为 `panda`、HEAD `4105c48d7c`，工作树干净；
版本化验收主记录仍将 R0.6 标为未验证，并将 R1 整体标为未通过，记录了 `md.connect`
原生 backend 的公共 SQL 旁路、部分 metadata/控制/远端资格和旧来源文本路线等缺口。
实施前须按实际交接核对并更新主记录；不得因进度说明或本计划把未验证、阻塞和跳过格写成通过。
R2 可先推进不依赖这些格的纯语义工作；需要真实来源读取的用例使用 R1 已取得的具体资格，
缺失资格的格子保持阻塞或未验证。

R2 主责为 C02.a/b/c、C17.a 的 Semantic/ontology 衔接，以及 C06、C08、C11–C13 在
Semantic 侧的声明前提。实现只依据已接受的 owning spec；目标文档与当前公共签名不同处，
先在对应 owning spec 明确本阶段的封闭类型、错误和身份规则，再同步实施。
R2 不把 R5 的 `members/read/observe`、R6 的统计计算、R7 的 Event/Lifecycle 执行、
R3 的统一 Analysis 图或 R4 的 Runtime/Store 偷带进本阶段。

R2 的出口是：一个真实 authoring 项目能经受限定义、`ms.load()`、精确 Ref、
`catalog.require(...)` 和 scoped readiness，产出稳定且可解释的业务定义与规范 Metric 图；
声明、推导、来源实测及一次分析的检查各有独立依据。非法版本/路径/单位/贡献/顺序组合
在负责的边界以结构化错误拒绝，不能由物理类型、样本值、ontology 或下游 pandas 补业务含义。

## 2. 工作包

### R2.1 统一身份、变量、版本与关系定义

收束 `semantic/{ir,resolver,_definition_identity,_authoring_*,typing}.py`、Ref 与 catalog 的
定义身份：Entity 的完整 `primary_key` 是业务身份 K；无版本、snapshot、validity 是封闭
表示变体，行唯一性分别为 K、`(K, snapshot_coordinate)` 和 `(K, valid_from)`。
版本选择保持精确 `at`/`before_end` 契约，不从分区名推断 snapshot，不自动取 last-known，
也不把历史行 `distinct(K)` 当成员域。静态加载检查声明及依赖，不做全源重复键或 validity
重叠预检；被消费时观察到违约仍由相应执行边界拒绝。

Dimension、TimeDimension、Measure 保持 owner、具体值型、单位、业务角色、时间依赖和
值政策的单一规范描述；技术写入时间不能代替声明的事件/状态时间。Relationship 明确方向、
端点、键、基数、可缺失性、角色及版本解析；多条业务路径必须明确选择，fanout 和坐标
重叠不能由 join 成功或数值巧合获得可加性。直接列、受限 Ibis 表达式和经投影的来源别名
在加载时走同一 Ref/依赖校验，不让列名或物理 schema 再造一个业务定义。

**交付：**规范身份/版本/变量/关系定义及其 Ref 解析；无 I/O 的声明正反例覆盖完整复合键、
精确版本边界、缺失快照、有效期、错误别名、歧义路径和不安全 fanout。R5 负责把这些规则
绑定到一次成员选择与实际来源行，不将 R2 静态声明记作来源数据已验证。

### R2.2 收束 Metric 声明、组件推导与单位/状态规则

装饰器的受限单一 Ibis return 继续由 `semantic/validator.py` 管；`@ms.metric` 的 root、
时间角色、unit、additivity 和适用的 null/empty/zero-denominator 政策由作者显式声明，
不解析函数体来猜业务公式、贡献组件或上卷权限。保留声明事实、builder 推导事实、
Analysis 检查义务和来源实测四种依据；定义版本和有效依赖进入规范 graph fingerprint。

以 `semantic/{metric_graph*,_metric_resolution,unit_algebra}.py` 的一份规范图承接
`aggregate`/`count`、`ratio`、`linear`、`weighted_mean`、cumulative 和适用的统计聚合。
每个组件分别保留计算根、过滤、路径、事件/状态时间、空间聚合、fold、累计 anchor、
unit、值政策、RequiredParts 与精确数值方法。ratio 只由显式 builder 保留分子/分母；
linear 检查相称单位，weighted mean 保留同一非 Null 配对的加权分子与权重和。
min/max/mean/count/distinct/median/percentile 的空、Null、精度和可合并状态各按方法
契约处理；opaque 表达式即使合法加载，也不因 additivity 标签获得原量归约权。

可加性绑定原生坐标与贡献许可；空间求和、时间 fold、半可加状态和非可加数值独立判定。
重叠标签的同一贡献不能在消去坐标后双计；空间聚合与时间 fold 不可交换时，保留足够
状态或拒绝该变换。R2 只推导内在需求，不依据某个 Population、Artifact 的现有部件或
后端路线授予一次操作的许可。`ms.statistical_weight` 的命名角色按 R0.3 契约加载和核对
单位/来源/身份；实际有限非负权重与完整分层参照由 R6 检查。

**交付：**一个规范 Metric 图及明确的声明/推导 provenance；独立手算的组件、单位、
Null/空值和 fold 反例；旧并行能力判断随消费者迁移删除，不能增加影子 registry 或
泛化回调。R5/R6 消费该图而不重新猜测 Metric 业务含义。

### R2.3 完成 Event、StateModel、业务顺序与日历声明

`semantic/{event,state_model,_authoring_temporal}.py` 保留 Event occurrence key 与
participant Subject K 的区别、明确的 `occurred_at` 业务轴、受限 occurrence predicate
及 `one`/`optional_one` 角色。StateModel 保留精确 Subject、封闭状态、inception、
确定性转移和有效事件角色；不把 Population、随访覆盖、censoring 或 replay window
写入语义定义。

按 R0.3 已接受的 `business_order` 契约登记同 Subject 的业务 sequence 或封闭的
simultaneous precedence，检查可比较性、唯一性、无环和依赖身份。同刻事件仅按 occurrence
ID 排序不构成业务顺序证据；若存在多个允许顺序，领域执行须在 R7 证明结果及保留部件
等价，否则拒绝。日历、认证期间和时间角色只提供定义及依赖事实；R5/R7 决定具体窗口、
格覆盖、fold 和领域输出。

**交付：**可加载的 Event/StateModel/order/calendar 定义与精确 Ref；同刻相反转移、错误
participant 身份、非法 sequence/precedence 和时间角色混用的正反例；向 R5/R7 交付
所需版本、角色、顺序及时间事实，不宣称 matcher/replayer 已通过。

### R2.4 对齐加载、披露、ontology 与阶段验收

`ms.load()` 仍是项目级静态校验事件；`catalog.require(ref)` 解析精确当前定义，
scoped readiness 在内存中检查请求的完整依赖闭包，`analysis_ready_inputs` 只返回直接
请求且无 blocker 的 Ref/封闭 runtime expression。preview 与 source-health 通过 R1
读取 owner 提供物理事实，分别保留采样/连接/业务检查的范围和状态；它们不改变 readiness，
也不将声明唯一性、样本或可选 metadata 变成全源证据。ontology 只关联规范 Ref 与后续
Artifact 身份，不授予因果、计算准入或自动分析计划。

同步更新 Semantic owning specs、公共 docstring/类型、原生 Help、错误 repair、`__all__`
快照、独立 drift/reachability/budget 测试及 `site/` latest 中英文示例；删除替换部分的
旧公开入口和消费者，不保留 alias、转发层或并行图。确需修改 packaged `marivo-semantic`
或 `marivo-analysis` skill 时，先取得 `AGENTS.md` 要求的明确用户批准；此计划不编辑 skill。
阶段主记录按 C02.a/b/c、C17.a 和受影响时间/领域前提逐格记录通过、失败、未验证、阻塞，
附代码 SHA、独立预期、命令和失败恢复条件；R1 留下的跳过或阻塞格不计入 R2 通过。

**交付：**可复核的真实 authoring 项目、公共披露和验收记录；R3/R5/R6/R7 接收精确
Ref、图、身份/时间/单位/部件需求及开放的运行检查义务，而非另一套业务定义解释器。

## 3. 核验与阶段出口

先运行触及的 Semantic/Ref/ontology 最窄正反例，再做真实项目加载、受限表达式、
版本/关系/Metric 图/Event/StateModel 的跨模块用例。可从
`tests/test_semantic_refs.py`、`test_semantic_phase2_validity.py`、
`test_semantic_metric_graph_lowering.py`、`test_semantic_event.py`、
`test_semantic_state_model.py`、`test_semantic_readiness.py`、
`test_semantic_authoring_snapshot_e2e.py` 和 `test_ontology_extension.py` 定位；
旧测试名只是入口线索，需保留能击穿新规则的独立 oracle。修改重复 fixture 时遵循
`marivo-test-fixtures` skill。读取相关集成只在已具资格的来源形态上断言，未实测后端
不可由 DuckDB、静态加载或编译结果代替。

使用 `make test TESTS='...'`、`make runtime-test TESTS='...'`、
`make typecheck TYPECHECK_TARGETS='...'` 和 `make lint-agent LINT_TARGETS='...'` 做定向核验；
共享/公共契约收口执行 `make check-agent`，API 文档与 `site/` 构建按主计划 §12.1 执行。
普通 R2 不运行完整 release-check。R2 仅在以下条件同时满足时标为通过：

1. C02 的 Entity/变量/Relationship/Metric/Event/StateModel/calendar 定义只由一套规范
   身份与 Ref/图表示，目标规则与当前 owning specs 一致；旧消费者没有第二份业务推断。
2. 声明、builder 推导、静态 ready、来源检查和一次 Analysis 准入的权限边界清晰；
   精确版本、单位、路径、贡献、Null/空值与业务顺序的正反例通过。
3. 真实 authoring 项目完成 `ms.load()`、精确 Ref、scoped readiness 与 Analysis 输入交接；
   公共类型/Help/错误/文档及 ontology 身份一致，未批准 skill 文件未改。
4. 相关测试、typing、lint 和文档门禁通过；R1 遗留、R5–R9 方法/后端资格按实际状态
   留在验收记录，不用跳过、fallback 或目标降级填平。

R2 的通过只证明业务定义及内在图契约，不证明六后端完整资格、Analysis 方法执行、
Artifact 冷恢复或最终安装包与真实 Agent 验收。
