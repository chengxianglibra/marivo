# R5：完整成员、观察、坐标与数值归约实施文档

Date: 2026-09-28

Status: R5.1-R5.6 bounded source-tree acceptance is recorded in evidence/r51-r56, including R5.5 review follow-up and the complete local R5.6 numeric matrix. R5 as a whole is not complete: remaining V11 historical skips/concurrency debt and installed-candidate closure remain in R5.7 and are unverified.

R5.1 的具体交付、消费者与逐格债务见[迁移清单](2026-09-28-marivo-full-algebra-dsl-r5-migration-ledger.md)；
[静态证据](evidence/r51/README.md)不授予执行资格。

## 1. 依据、起点与完成定义

执行[总实施计划 R5](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md#r5--完整成员观察坐标与数值归约)，
承接 C03–C06/C10 及总计划 §10–§13 的方法、恢复和工程义务。语义依据为
[分析代数](2026-09-23-analysis-algebra-theory.md)、
[DSL 接口设计 §5](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md#5-观察分组与两种归约)和
[执行架构](2026-09-24-marivo-analysis-dsl-architecture-design.md)。前置交接见
[R4 实施文档](2026-09-28-marivo-full-algebra-dsl-r4-implementation-plan.md)与
[验收主记录](2026-09-26-marivo-full-refactor-acceptance.md)。

用户确认 R0/R1/R2/R3/R4 任务完成，以此启动 R5 规划。起草分支为 `panda`，HEAD 为
`fabd277a8d4510dce3973bd772f35175ea8c42e4`；工作区含 R4.6 的未提交代码、测试、文档和
证据。当前验收记录已记载 R4 在 J1–J4 与本机资格边界内收口；本计划不重做其验收，
不将前置完成解释成全部数值、时间、后端或真实 Agent 已获资格。实施前记录实际 SHA、
未提交 diff 和新增文件 digest，沿用最终 R4.6 交接，不覆盖其他工作。

R5 完成意味着：完整成员、属性和观察通过现有统一图、方法注册、Runtime 与 Store 7
执行；多根各自保留贡献身份与状态，完整坐标不放大或伪造；部分坐标消去、时间粗化及
数值归约按具体方法准入；所有承诺的 K（保留续算能力）在断源新进程中实际成立。
公共路径、方法状态、披露、测试和安装包同步切换，不保留已迁方法的旧执行链。

## 2. 范围、责任与契约 owner

| 能力 | R5 交付 | 明确边界 |
| --- | --- | --- |
| C03 成员与属性 | 完整复合主键、无版本/snapshot/validity、精确 `at`/`before_end`；数值/分类/时间/布尔 read；成员选择与主体投影 | Journey/Interval 的领域主体映射由 R7 验证；R5 提供通用集合像规则 |
| C04 观察 | 无窗口/有限窗口/时点、完整根与组件出现位置、分支过滤和路径；runtime_metric 五工厂 | 普通 Relation ratio、完整比较和归因由 R6 承接；opaque Metric 不反推组件 |
| C05 坐标与归约 | Entity×Time、贡献坐标完整元组并集、组合分组、显式空组、部分轴消去；原状态与 RowStatistic | 不增加任意笛卡尔积、动态字段字典或与 group_by 同义的入口 |
| C06 时间 | 四种时间角色、三种时区权威、DST、认证期间/occurrence、累计、半可加 fold 与时间粗化 | 不把重叠窗口当分区，不授予无状态的时间上卷 |
| C10 直接观察 | exact distinct、线性插值 quantile、显式 approximate 许可和实际算法披露 | 首次无原量 rollup/归因；完整六后端资格归 R9 |

R5 在本机真实 DuckDB table/Parquet 与所需 SQLite 路线上取得具体方法资格；SQLite
纳入范围是为了闭合已明确交接的时间/fold 正例，不等待 R9 才处理本阶段债务。
其余后端保留精确拒绝和 R9 交接，不能凭 Ibis 编译成功或 R1 adapter 通过授予方法资格。
Decimal、Duration、时间类型按方法建立接受/拒绝矩阵，不能笼统宣称全类型支持，也不能
把总计划要求的必需格全部列为拒绝后宣布 R5 完成。

R2.2 已按用户要求撤回 `ms.statistical_weight`；R5.1 再次确认该范围决策。
`mv.statistical_weight`、其命名角色/Ref/Catalog/Help 及依赖它的当前行 weighted_mean
不属于 R5 必需目标，也不作为阶段阻塞项；不自动转授 R6，重新考虑须有独立范围决策。
已有 Metric 与 runtime_metric weighted_mean 的原组件语义仍在 R5 范围内。
完整 reference_weights、standardize、固定参照与分配等其余 C08 工作仍归 R6。

| 唯一 owner | R5 需要闭合的事实 |
| --- | --- |
| [Semantic object model](../../specs/semantic/semantic-object-model.md) | Entity 版本、字段 kind、组件与 opaque 声明、贡献/统计单位、空/Null、时间 fold、Metric 权重组件及 quantile 精确性 |
| [Loading/validation](../../specs/semantic/loading-validation-introspection.md) | Ref 和 runtime expression 的解析、依赖闭包、时间/路径事实与 readiness；不制造执行证明 |
| [Python Analysis design](../../specs/analysis/python-analysis-design.md) | 具体公共变体、签名、域/坐标/绑定、单一图准入及 source/fixed 边界 |
| [Methods and states](../../specs/analysis/operators-and-frames.md) | 每方法规则、前提、Cell、部件、状态版本、空状态和动态 K |
| [Timezones and calendars](../../specs/analysis/timezone-and-calendar-design.md)、[Temporal semantics](../../specs/temporal-semantics.md) | report/source/calendar 权威、边界、格归属、累计及物理时间精度 |
| [Session/Runtime](../../specs/analysis/session-state-and-runtime.md) | 身份、共享、统一交换、receipt、原子发布、固定命中和冷恢复 |
| [验收主记录](2026-09-26-marivo-full-refactor-acceptance.md) | 每格资格、旧链删除、历史失败/skip 恢复、证据与下阶段交接 |

总计划和接口设计是目标，当前 owning specs 中的 historical/inactive 段不是现行物理资格。
每包先在上述 owner 接受精确增量，再改产品。不得在本计划另造第二份注册表、状态协议
或公共 API 名单；新增公开类型和 Help target 必须进入现有 disclosure-contract 检查。

## 3. 必须保持的语义与执行约束

### 3.1 成员、版本与属性

- 根 members 投影 Entity 的完整主键，信任声明，不默认 distinct 或增加全源唯一性预检。
  版本坐标用于选版本，不并入稳定 Entity 身份。snapshot 选择指定快照，validity 遵守
  声明区间；缺版本、重叠版本和本次消费发现的重复身份不得以最近版本/首行/去重修补。
- `at=instant` 与 `before_end` 是不同类型化选择；后者按边界语义解析，禁止 epsilon。
  成员版本、属性时点、指标贡献窗口和输出坐标四者各自绑定，不从一个推断另一个。
- read 按字段 kind 返回具体关系；多值映射拒绝，不自动聚合。属性版本、路径角色及
  单值/覆盖依据进入图。无版本定义不能因传入 `at` 获得历史事实。
- Entity 筛选后的 members 直接投影，已有单射依据保留；多实例映射主体取得完整键
  集合像，source 用 Ibis distinct，fixed 用同义本地规则。不得用该规则掩盖输入违约。

### 3.2 多根贡献、运行期量和覆盖

观察输入为 Metric Ref 或 RuntimeMetricExpr 的闭合联合。
保留 `aggregate/slice/ratio/linear/weighted_mean` 五工厂；收紧 SliceValue 类型，不接收
SQL、裸字符串业务身份、任意回调或宽泛 `Any`。`linear` 的有序 +1/-1 项不是时期 Difference。

每个组件**出现位置**独立绑定根、筛选、路径/角色、版本、时间、贡献单位与实际输入。
同根不同过滤不能合并；不同根先独立归约，再按完整目标键组合，禁止事实表相乘后求和。
无窗口表示未加本次时间限制的获准来源，不意味着全历史完整；要求端点的 Metric 不能
退化为无窗口。`slice` 限定贡献分支，`where` 限定结果域，两者不能互换。

ratio 保留各命名原组件和 finish/零分母政策；runtime `zero_division="null"` 对应
`Undefined(zero_denominator)`，不是 Null。Metric mean/weighted_mean 合并原组件；
当前行 mean 创建新统计量。opaque Metric 只消费声明许可和实际具备的状态，
不得通过函数体、结果列名或相同数值反推可归约的组件。

### 3.3 坐标、分组与两种归约

每组件在自己的范围内产生完整元组像 D_j，共同域为完整元组的并集；不是各列投影的
笛卡尔积、根间交集或仅分子域。坐标在最终值抵消为零时仍保留。缺组件只有在覆盖足以
证明合法空贡献时才能生成方法空状态；未知覆盖、缺映射、Null 与执行失败不能补零。

成员域直接 group_by 自身 Dimension Ref 是惰性属性依赖；Relation 上的 Ref 只引用
已保留且唯一绑定的坐标，不隐式重读属性或找回细域。分类键必须 Defined 且在值域内，
不生成隐含 NULL 组或丢弃缺键事实。检查仅作用于实际消费域，不能扩成无关全表验证。
多个分类输入按完整键对应，禁止按行号对齐；`groups=target_groups` 保存显式目标及空组。

`each(grid)` 是有界时间乘积；加入数据驱动贡献坐标后，结果不再自动覆盖完整 Entity×Time。
恢复完整目标必须经 `group_by(..., groups=...)` 和覆盖/空状态准入，不伪造未知类别。
`group_by` 同时表达保留轴、部分消去及唯一时间轴的 Grain 粗化；跨月的一周不能拆成
两个目标月。无参 rollup 消去全部坐标，不能替代部分轴上卷。

rollup 合并原状态，再 finish；summarize 将当前行作为新贡献。sum/mean 默认要求
参与值 Defined 且有限；count 计全部当前实例，count_defined 计 Defined 标签。
合法空组保留 count=0、sum=0、mean=Undefined(empty_mean) 及其有效状态；状态缺失
不能当空状态。RowStatistic 只披露已注册并验证的状态续算，不能冒充原 Metric。
依赖已撤回命名角色的当前行 weighted_mean 不在本次目标；Metric weighted_mean 保留
原有同一非 Null 配对及其声明的零/缺权重政策，不能用它替代一个新的当前行统计量。

### 3.4 时间、精度与数值

report timezone 来自持久 Session，source timezone 来自已解析来源权威，calendar
timezone 来自认证快照。date 保持 civil date；aware timestamp 保持 instant；naive
timestamp 按明确权威解析，DST gap/fold 与引擎规则不一致必须按契约拒绝。
固定恢复不重新解析主机时区或探测源。所有时间校验业务读取同样由 Ibis 表达。

时间格保留实际半开边界、不完整格、时间精度与认证 snapshot digest。period/occurrence
继续由现有 Catalog 解析为 TimeScope；重叠 occurrence 不成为可求和分区。
累计 `[anchor(e), e)` 不因展示起点截断历史；all-history、grain-to-date、trailing
保持各自 anchor 和覆盖。固定秒长 trailing 与 23/25 小时 civil day 不得混淆。
空间聚合和半可加时间 fold 不默认交换；消轴必须验证贡献重叠、单位、覆盖、顺序与
RequiredParts，状态不足则拒绝，不能对状态值/累计值直接求和。

数值矩阵逐方法列明输入/输出类型、状态类型、空/Null、溢出、非有限值、Decimal scale、
舍入和误差标准，Duration 与 timestamp 不得笼统强转 float。不同批次/行序/状态归约树
须符合该方法标准。exact distinct 保留完整身份含义；exact quantile 按线性插值独立验证。
approximate 是许可而非强制算法，无自动 exact→approx 降级；披露实际实现及其保证，
不编造误差界。首次 distinct/quantile 不保存未承诺的分布/sketch，不开放原量 rollup/归因。

### 3.5 统一 Runtime 与固定续算

扩展 R3/R4 的 core/rules、method registry、GraphPlan/LoweredPlan 与现有 Runtime，
不建立 observation 专属执行器。纯构造不读业务行；沿用必要的 R1 schema-only 预检边界，
静态不合法/mixed/跨 Session 输入在业务读取及 Run 前拒绝。数据相关检查作为有作用域
的执行义务，不能把未运行检查记为 completed。选定路线失败不再选路。

source 顶层 execute 取得新求值，显式共享节点只实现一次；fixed 绑定精确且有序的输入、
receipt、方法/状态版本及部件，校验后才能命中。fixed-only 使用受控 Arrow/Parquet→pandas，
不得回到 DuckDB/远端；恢复不加载当前 Semantic 来补定义。主表、组件、坐标、成员映射、
覆盖与时间部件全部通过共同交换/发布框架；必要状态缺失、损坏或版本不符即拒绝。
新增方法状态在既有版本机制内闭合，不凭阶段号新增 Store 代际；若协议确需变化，先在
Runtime owner 接受明确破坏性规则，无双读、迁移或自动重建用户状态。

## 4. 分包实施顺序与出口

每包形成契约、实现、公共消费者、删除和对应验证的完整切片；私有正例不能冒充公共资格。
下表为顺序工作包，不要求创建同名模块。

| 包 | 工作与主要定位入口 | 可验证出口 |
| --- | --- | --- |
| R5.1 契约与迁移清单冻结 | 对照 C03–C06/C10，盘点 `public_dsl.py`、`graph_members.py`、`graph_observation.py`、旧 observation/compiler、runtime_metric 与测试；在 owner 冻结具体签名、规则、类型/状态、角色、时间与物理资格矩阵 | 每个必需变体、旧消费者、14 skip/8 fail 有 owner 和测试；关键目标无未决占位；明确已撤回统计权重接口的排除边界 |
| R5.2 完整成员与 read | 扩展成员图、source bindings、规则与 Ibis lowering；接入完整键、版本选择、四类属性、筛选后 members 及集合像 | 精确版本/边界、完整键、单值/覆盖、重复身份反例通过；根投影没有 distinct/全源预检；source/fixed 成员与部件一致 |
| R5.3 多根与 runtime_metric | 统一 `semantic/runtime_metric*.py` 与 `analysis/runtime_metric.py` 消费规范图；迁入五工厂、独立组件/分支、无窗口和既有有限窗口观察；原组件数值状态随方法接入 | 三根以上、同根不同过滤、不同事实粒度、路径角色/贡献单位及 opaque 正反例；独立 raw-fact oracle 不放大；原组件与行统计分离 |
| R5.4 坐标、目标组与部分归约 | 扩展完整元组域、组合分组、显式 groups、部分轴消去、成员/坐标状态运输；接入当前行 count/count_defined/sum/min/max/mean 与承诺的 RowStatistic 续算 | 完整并集、零值坐标、空组、严格分类、无笛卡尔放大；L8/L9 在同目标域与真实状态上验证；丢部件撤销 K |
| R5.5 时间网格与 fold | 在现有 temporal/calendar owner 接入 each/grid/边界句柄、状态时点、认证窗口、三时区/DST、累计与半可加顺序；替换对应旧 temporal SQL | Entity×Time、部分格、时间粗化、重叠和非交换反例；DuckDB/SQLite 所需实源路径与断源 fixed fold 通过；所有检查读取可追溯 Ibis |
| R5.6 数值资格与直接观察补全 | 扩展 Metric weighted_mean 与剩余数值类型资格，distinct/quantile 定义侧精确／近似区分；校验 min/max/mean/加权/时间状态与版本 | 精确/近似无静默替换；Decimal/Duration/精度/溢出矩阵明确；所有必需方法已闭合，distinct/quantile 原量上卷拒绝，当前行统计仍可单独成立 |
| R5.7 冷恢复、债务与安装收口 | 逐方法核对 graph protocol/receipt/K；迁移遗留正例与并发用例；反查注册/SQL/codec/Help/包内消费者；同一候选 wheel 重跑旅程 | §6 全部必需格取得证据，旧 R5 链退出产品及测试消费者；14 skip 与 8 fail 按 §5 逐项处置；R6/R9 交接明确 |

R5.3/R5.4 先在既有时间形状下形成可执行切片；R5.5 扩展网格与时间消轴；R5.6 完成数值
矩阵。不得因最终收口包存在而把每包的 Help、类型、错误、英中示例或状态测试拖到最后。
R5.1 只冻结契约不冒充实现；任何被保留的目标缺口均阻止相应后续包及整个 R5 完成。

## 5. 删除、历史失败与消费者迁移

| 当前定位 | 迁移/删除要求 |
| --- | --- |
| `public_dsl.py`、`materialization/graph_members.py`、`graph_observation.py`、`graph_composition.py` | 扩展现行入口并移除单列身份、有限根/坐标、UTC-only 等被新资格替代的限制；无新旧双入口 |
| `observation/{population,metric,aggregation,coordinates,rollup,temporal}.py` 及旧 contracts | 提取仍有效业务算法/独立预期，接入 core/methods；删除已迁方法旧节点、重复准入和注册，不保留转发 shim |
| `compiler/temporal.py`、`materialization/temporal_sql.py`、旧数值/分位/折叠路线 | 按实际调用反查逐项替换为 Ibis 或事先注册的本地算法；连同 validation/probe SQL 检查，不改名藏入 adapter |
| `operators/registry.py`、`compiler/source_admission.py`、`materialization/dataset_execution.py` | 移除完成迁移的 R5 旧分派和阻断分支；R6–R8 残留按真实消费者登记，不提前删共享依赖或扩大默认资格 |
| 旧 codec/publication/retained 路线与 v6 私有 harness | R5 消费者迁入现有 Store 7；无消费者项删除，跨阶段项明确最终 owner；不能为恢复测试重新开 v6 公共路径 |
| `semantic/_quantile.py` 与 runtime_metric 消费者 | 精确／近似由 Metric 聚合定义表达；观察侧不接受 accuracy 或 method，无兼容别名；算法身份保留于计划/结果证据；不支持精确定义时提示对应近似定义并核对源端支持 |

上表是定位入口，R5.1 必须补齐实际 import/call/registration 清单；不是按历史文件名重建模块。
保留独立业务预期和故障语义，旧 API 测试按唯一新入口改写，不能要求旧签名继续可用。

[验收主记录中的 14 项 R5 正例](2026-09-26-marivo-full-refactor-acceptance.md)逐项恢复：

| 原测试位置 | 参数格数 | 恢复责任 |
| --- | ---: | --- |
| `test_lazy_source_algebra.py::test_semantic_calendar_validations_publish_each_required_occurrence` | 2 | R5.5 认证窗口/校验发布 |
| `test_sqlite_semantic_integration.py::test_sqlite_agent_native_authoring_journey` | 1 | R5.5 SQLite 时间观察；该测试名不等于真实 Agent 证据 |
| `test_lazy_local_placement.py::test_required_parts_place_locally_without_worker_or_origin_work` | 1 | R5.3/R5.4 mean 状态与固定放置 |
| `test_lazy_local_placement.py::test_diagnostic_version_does_not_change_selection_or_execution` | 4 | R5.3/R5.4 结果选择及诊断版本不改变执行 |
| `test_lazy_status_fold_admission.py::test_sqlite_fold_spatial_sum_matches_hand_computed_constants` | 5 | R5.5 SQLite fold 顺序与手算常数 |
| `test_lazy_retained_compiler.py::test_retained_filter_aggregate_joins_exact_component_parts` | 1 | R5.3/R5.4 多根状态按完整键运输 |

依赖闭合即移除对应 skip，迁移调用形状并保留独立预期，再运行相关测试及 broad gate。
不能删除数值断言、改成仅验证拒绝或增加 xfail 来收口；如具体断言与已接受的新契约冲突，
记录原业务义务、新契约与替代 oracle 的一一对应，不用测试数量掩盖覆盖损失。

`tests/test_lazy_runtime_concurrency.py` 的 8 项旧来源失败已有
[基线日志](evidence/r44/baseline-concurrency.log)与
[诊断摘要](evidence/r44/runtime-initial-observation.md)。将等价并发/取消/故障场景迁入
唯一公共图链，保留各场景时序与不重放/不误发布断言，按实际新 owner 安装钩子；
不通过恢复 `session.observe` 或仅等待旧钩子解决。R4 的新图并发通过不自动清除这些格。

## 6. 验收矩阵与独立 oracle

每行同时登记 Logical/source、Materialized/fixed、恢复后 K、实际类型/路线及拒绝格。
未运行、阻塞、失败和通过分别记录；目前下列 R5 格均为**未验证**。

| ID | 核心验证与独立预期 | 必须击穿的反例 |
| --- | --- | --- |
| V01 成员 | 手列完整复合身份、snapshot 精确匹配、validity 边界、before_end、筛选/集合像；Ibis 计划及实际读取审计 | 同首列不同次键、历史 distinct、last-known、重复版本、根默认去重/预检 |
| V02 read/路径 | 四类值、明确属性时点、完整键对应、角色/版本与覆盖 | 多值取首行、错角色、跨 Session、历史属性默认最新、无版本伪历史 |
| V03 多根 | 原始事实分别聚合，Fraction/Decimal 独立计算；至少三根、同根两分支、不等粒度 | join fanout、分支过滤泄漏、同表独立叶错误合并、单位相同替换量 |
| V04 坐标 | 手列完整元组并集、抵消为零、显式空组与贡献限制域 | 投影笛卡尔积、只取分子/交集、缺覆盖补零、Null 类别、按行号拼接 |
| V05 两种归约 | AOV 当前行均值 50.5 与原组件 200/101；四态 count/count_defined；空 mean 状态；L8/L9 | 均值的均值、丢空组、缺状态当空、Undefined 值冒充零、RowStatistic 冒充原量 |
| V06 时间 | 独立列举边界/期间和 UTC instants，23/25 小时 DST、部分格、认证 digest、完整 Entity×Time | epsilon、date 偏移、时区重开漂移、跨月周拆分、occurrence 重叠求和 |
| V07 fold/累计 | 原始样本按声明空间→时间顺序计算；直接/合法分层状态及单位核对 | 两设备不同峰时刻、非交换 min/max/percentile、累计重复计数、展示起点截断历史、缺覆盖 |
| V08 数值/权重 | Decimal/大整数/Duration、非有限/溢出；Metric 同一非 Null 配对及零权重政策；撤回的当前行权重不激活 | float 失真、孤立 Null 配对权重、零权重政策混淆、错组件、统计单位混用 |
| V09 distinct/quantile | 手列唯一完整身份和排序线性插值；空/Null/q 边界；actual algorithm / definition-owned exactness | exact 自动近似、bool q、平均/再求分位冒充总体分位、无状态原量上卷 |
| V10 运行与恢复 | 每类状态 source 新求值、共享计数、fixed 精确命中；断源/删模型新进程执行承诺 K | mixed 提前读取、只比 show 不续算、坏 part/receipt/版本、fixed 打开 DuckDB、失败换路 |
| V11 债务与删除 | 14 个正例映射、8 个并发失败映射、旧注册/import/SQL/codec 的消费者反查 | 删除独立预期、skip 算通过、测试私有新链但公共入口仍走旧链 |
| V12 披露与安装 | typing 正负例、导出快照、Help 可达/预算、动态 K/错误、CLI/英中示例、同 wheel 公共旅程 | 新公开符号无 owner、静态帮助夸大资格、包内缺模块、源码污染或不同 wheel 拼证据 |

独立预期不调用被测产品聚合、时间转换、状态 finish 或同一 lowering helper。
L8 比较直接和分层的真实状态；L9 固定同一个含空组目标域；L6 比较恢复前后实际 K 和
结果，按值等价、语义等价、K 等价分别断言。数值一致不自动证明后两者。
共享来源路线与 pandas 路线使用同一业务向量，但各自对独立 oracle 核对。
扩展交换/状态必须覆盖空流、分批、部件换序、未耗尽、关闭异常和发布前拒绝。

## 7. 门禁、证据与交付

实施时使用仓库 `marivo-test-fixtures` skill 处理测试/夹具修改。先执行最窄相关范围，
实际新增文件名在各包交接登记；以下是现有回归入口，不表示它们已覆盖全部 R5：

```bash
make test TESTS='tests/test_analysis_runtime_metric.py tests/test_semantic_r22_metric_graph.py tests/test_analysis_graph_preflight_r45.py'
make test TESTS='tests/test_lazy_source_algebra.py tests/test_lazy_local_placement.py tests/test_lazy_status_fold_admission.py tests/test_lazy_retained_compiler.py tests/test_sqlite_semantic_integration.py'
make runtime-test TESTS='tests/test_lazy_runtime_concurrency.py tests/test_lazy_temporal_public_runtime.py tests/test_analysis_graph_publication_r44.py'
make check-agent
npm --prefix site run build
git diff --check
```

Python 修改另按实际文件执行 `make typecheck TYPECHECK_TARGETS='...'` 与
`make lint-agent LINT_TARGETS='...'`；占位符不能作为实际命令记录。
共享行为按仓库要求拓宽默认测试，最终 `make check-agent` 包含 broad lint/typecheck/
default tests/API docs；Runtime 必须单独提供非 skipped 证据。不为普通阶段启动 MinIO
或运行完整 release-check；六后端完整资格归 R9，真实 Agent 能力簇归 R10。

公共签名、类型、Help/CLI、repr/show/contract、错误 repair、英中 latest 示例在相应包同步。
packaged `marivo-semantic`/`marivo-analysis` skills 的修改按 AGENTS.md 需要用户明确批准；
本次文档不构成该批准。实施时先形成具体对齐清单；若必须编辑，单独取得该范围授权，
未获授权时将技能对齐明确记为未完成，不把披露门禁整体判为通过。

R5.7 用同一候选非 editable wheel，在仓库外、记录 site-packages 来源与依赖的隔离环境
运行公开旅程：版本成员/read；三根分支与完整坐标；网格/fold；当前行与原状态；
distinct/quantile。对承诺 fixed K 的结果执行 produce/continue/recover 独立进程，
断源后阻断 Semantic 加载、来源连接与 DuckDB，比较实际续算和命中；无 K 的操作验证
明确拒绝。source-only 的新观察不伪装成恢复后的固定续算。

证据建议保存在受版本控制的 `docs/superpowers/specs/evidence/r5/`，实施时才创建：
manifest 记录 requirement/method/version、代码与 dirty-tree digest、输入/oracle digest、
依赖/后端/来源形态/类型/路线、命令和退出状态、日志、receipt/状态/部件及 wheel hash。
大型产物可外置，但索引须给可取得位置和 hash，不能只有临时目录。主验收记录逐包追加，
不回填历史失败为通过，不将当前计划写成验收记录。

最终交接列出新增/修改 owner、公共与状态契约增量、已删除链路及 SQL、所有必需格结果，
以及 R6 关系/参照/归因、R7 领域映射、R8 统计扩展、R9 后端、R10 Agent 的精确剩余项。
R5 收口要求本阶段必需格无开放阻塞，14/8 债务逐项可核对；文件改名、静态扫描或
几条数值正例均不能替代完整出口。R5.1 不实施产品、不提交、推送或发布。

## 8. R5.1 冻结交接（2026-09-28）

实际基线为 `panda` / `dd42c7cca070fba60d6a2761331d86dbaca135ce`；R4.6 已提交，
初始 staged/unstaged diff 均为空，仅本实施计划未跟踪。此前起草时的 dirty-tree 描述
保留为历史背景；本次以 evidence/r51/baseline.json 为准。

已冻结成员/read/观察/路由及五工厂、完整域/目标组/原状态与行统计、时间/数值矩阵、
统一 Store 7 状态运输和冷恢复要求；具体契约仅在 §2 列出的 owning specs 维护。
消费者迁移、14 个 skip、8 个历史失败及 V01–V12 的逐项映射由迁移清单维护。
本次只验证文档与静态映射，不解除历史 skip/失败，不改变 R4 资格或宣称 R5 已完成。

## 9. R5.2 实施交接（2026-09-29）

基线 `panda` / `75d573e87c6a48aa337c3be170c87baa87d5a15c`，初始工作区干净。
完整复合身份、声明版本、四类直接列属性及单值路径、消费域检查、source/fixed
筛选和完整 Subject 集合像已接入既有图与 Store 7。
[V01/V02 与本包 V10/V12 证据](evidence/r52/README.md)记录独立预期、执行路线、
日志与候选指纹。80 项定向 Runtime、5363 项默认测试通过；19 个原有跳过仍保留。
站点、API、typing、lint 与 whitespace 门禁通过。旧 v7 快照缺少新增冻结字段时明确
拒绝，无兼容重建。本包不代表完整 R5、安装 wheel 或真实 Agent 验收。
R5.3–R5.7 及 D01–D22 不改判；未提交、推送或发布。

## 10. R5.3 实施交接（2026-09-29，审查后收口）

候选基线 `panda` / `09a1ef3b73e9cf2420b7e3ee087dcbe8727c3a68`。
当前工作承接已有未提交变更；本节取代先前 15/26 项用例及「weighted_mean 全部移交
R5.6」的交接结论。最终命令、候选摘要与矩阵见 [R5.3 证据](evidence/r53/README.md)。

五工厂已在既有公共图、Ibis/SourceSession 与 Store 7 路线上形成可执行切片。
每个 canonical component 独立绑定根、过滤、路由与时间范围，先归约再按完整目标键
组合；复合实体主键回归覆盖同首列、不同租户，以及 sum/ratio 和 fixed rollup。
三根、同根不同过滤、不同事实粒度、错误根角色、多余路由、单位冲突均有独立回归。
opaque 的正例是目录加载与 require；负例是不得从相等数值推断贡献图及原量上卷许可，
不把 opaque 直接观察声明为已支持。

`weighted_mean` 新增自己的观察规则、精确方法、四项 int64 配对状态、源端与固定
上卷及一致性校验。catalog/runtime × table/Parquet 均执行；成员 A=1000/3、C=400，
总量必须合并成 2600/7，而不是平均成员均值。零权重和、空贡献、单侧 Null、损坏
状态、缺文件及来源/语义模型离线的新进程恢复均有验证。R5.6 继续承担其他数值类型
和完整精度/溢出矩阵，不再接收「整个 weighted_mean 尚未实现」的债务。

linear 保留每项 empty policy，支持嵌套正负 sum/count 项及 source/fixed 原量上卷；
`revenue - (revenue - count)` 成员为 1/1/1、原量合计 3。ratio 各侧可为 sum/count，
保留空贡献与零分母区别。源端及 exchange 的方法→状态类型映射改用 MethodSemantics
唯一语义所有者，wire 角色表仍为显式协议约束。

公开 observe 的 typing 与运行时一致：during 可省略；Metric Ref 返回 numeric/ratio
联合，由实际图决定家族；linear/weighted 返回 numeric，ratio 返回 ratio。正负 typing
断言、Help 和当前中英文示例同步。packaged skills 与 AGENTS.md 未改动。

**当前资格边界**：DuckDB table/Parquet、UTC instant-us、既有 1–2 跳路由；weighted
是直接 int64 数值/权重且不带贡献坐标，ratio/linear 为 int64 sum/count 叶。
复合 ratio/linear 外层 slice 经 canonicalization 下推至各叶，已执行验证；
嵌套 nonlinear finish 与 ratio error policy 没有获得新资格。构造器可表示的图不等于每个组合
已有执行资格。256 KiB continuation 预算保持，17 项压力探针仍拒绝；不扩大预算。
坐标与分组归 R5.4，时间归 R5.5，完整数值矩阵归 R5.6，wheel/全恢复矩阵归 R5.7。

按 §3.5 显式记录：Ref 字段扩为闭 runtime 联合；OriginalStatePart 增加逐组件
empty_rules；ratio 改为双方 magnitude/support 四列；新增 weighted 四项原状态及
方法。无兼容双读、无新 Store 代际，不声称旧冻结状态编码保持不变。
D01–D22 未解除；D04/D05–D08/D14 既有 skips 保留。M10 source_admission 不在公共图
路径，仍有 R6–R8 消费者，保留共享代码。未提交、推送或发布。

## 12. R5.5 实施记录（2026-09-29）

实际启动基线为 `panda` / `3eede8b61e2e7cdeb3e43c78333ad59e8e4777c5`，首次工作区干净；
本次续作 HEAD 为 `8dccde674ed8a4586b2b4b1ff39d017a39ec44e1`（既有证据忽略规则提交）；
先记录继承的 dirty-tree 指纹，保留前轮全部修改。用户方案中的旧 HEAD
没有被检出或覆盖。可版本控制的资格摘要与复现入口见[迁移清单](2026-09-28-marivo-full-algebra-dsl-r5-migration-ledger.md#r55-qualification-summary)。启动、续作及候选指纹保存在本地忽略目录 `evidence/r55/`。

已接入公共网格和完整 Entity×Time、逐格贡献窗口、独立属性版本端点及符号 before_end、
空格与部分格、保留时间轴及整格粗化。累计以每个 occurrence 的实际端点读取
all-history、grain-to-date 或固定秒长 trailing；展示起点不截断历史，重叠累计拒绝消轴。
半可加量在采样时点先求声明的空间 sum，再做 first/last/mean/min/max；后续空间
上卷要求对齐的 pre-fold 样本，错峰设备反例直接与固定分层结果均为 10。

DuckDB table/Parquet 与所需 SQLite UTC/date 路线均走统一方法、Ibis/SourceSession、
共同交换、receipt 和 Store 7。来源时间按独立权威转换并读取校验原值/引擎值，
拒绝 DST gap/fold、规则不一致和不具资格的精度。日期保持 civil date。每个观察
occurrence 的认证检查记录 origin/scope/digest；重叠命名 occurrence 不被当成一个分区。

D01–D03、D09–D13 的 8 个历史参数格恢复，保留 `[110,30]`、SQLite revenue `30`
以及五种 fold 的 `110/70/90/50/130` 独立预期。产出、断源续算、冷恢复运行于独立
进程，比较实际结果、状态和 K，并注入部件、receipt、版本损坏验证续算撤销。
公共 Help、类型、动态指导及英中 latest 示例同步。实际 import/call 迁移和共享
R6–R9 owner 见迁移清单；未按文件名删除共享逻辑。

资格范围、门禁结果及复现命令以迁移清单中的版本化摘要为准；本地证据目录补充原始日志。
完整数值资格仍由 R5.6 承担，安装包和
总体债务收口由 R5.7 承担；没有扩大后端范围或恢复 statistical_weight。
AGENTS.md、packaged skills 未修改；没有提交、推送、发布、release-check、MinIO、
wheel 或真实 Agent 验收。

本包门禁：`make check-agent` 通过（5405 passed、5 skipped）；相关 Runtime
193 passed，另有标量累计端点 3 passed、最终 fold 回归及溢出拒绝 6 passed。
站点构建与 whitespace 检查通过；精确命令、退出码和候选指纹均保存在证据目录。

### R5.5 review follow-up

The three review findings are repaired: fixed/cumulative DATE windows keep their
own timezone on a foreign-zone grid; grid construction uses incremental boundary
checks and skips the historical prefix for fixed-offset zones; zero-row fold
continuation retains explicit Arrow types. Regressions include repeated-hour
minute boundaries and source-offline empty grouped/singleton fold continuation.
The versioned qualification summary and reproduction commands are in the migration
ledger. Detailed local logs and fingerprints are in ignored `evidence/r55/review-fixes-*`;
those local files are not prerequisites for reading or reproducing this record.

## R5.6 accepted contract amendments (2026-09-29)

Metric definitions own exact/approximate aggregation through distinct AggKind
variants; the public `quantile_metric` wrapper and observation-side `method` /
`accuracy` selection are removed without aliases. Source aggregation must execute
as Ibis-compiled SQL. Source-native quantile precision loss is accepted when the
actual algorithm, output type and limitations are disclosed. Fetching contribution
vectors for local sorting or rational interpolation is not permitted. Unsupported
exact definitions name their corresponding approximate declaration and explain
whether the current backend supports it; execution never substitutes it.

R5.6 source-tree implementation and numeric-matrix verification are complete.
The [migration ledger](2026-09-28-marivo-full-algebra-dsl-r5-migration-ledger.md#r56-implementation-checkpoint)
records exact method/type/source/fixed boundaries, independent oracles, cold
recovery, version/corruption rejection and reproduction commands. Related Runtime
acceptance totals 315 cases in separate batches; `make check-agent` passes with
5432 tests and 5 skips. Typing, site build and whitespace checks pass. No wheel,
release, remote R9 or real Agent qualification is inferred; R5.7 remains open.
