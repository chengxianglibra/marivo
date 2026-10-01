# Marivo 全量分析代数与 Analysis DSL：R6 实施文档

Date: 2026-09-30

Status: R6.1 契约冻结完成；R6.2 已完成账本列明的公共对应、比较与 source/fixed 资格验证；R6.3 已完成账本列明的谓词、完整 Entity×Time cohort 与固定恢复资格验证；R6.4 已接通固定参照与标准化，实际资格及门禁记录见 ledger；R6.5 已接通排名与终端展示，实际资格及门禁记录见 ledger；R6.6 已接通归因与部件运输，实际资格及门禁记录见 ledger；R6.7 尚未开始。

R6.1 的实际基线、owner 决策、消费者清单及验证见
[R6 migration ledger](2026-09-30-marivo-full-algebra-dsl-r6-migration-ledger.md)。
下述起草基线保留为历史；实施基线为 `44ff8478a740c60b23fc1566a52523acaa476851`，
tracked 工作树干净，仅本计划未跟踪。

## 1. 目标、前置交接与权威

执行[总实施计划 R6](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md#r6--完整关系组合比较参照与归因)，
完成 C07–C09 的关系组合、比较、选择、cohort、参照、排名展示及归因。
语义依据为[分析代数](2026-09-23-analysis-algebra-theory.md)、
[DSL 接口设计 §5.6、§6–§7](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md)
及[执行架构](2026-09-24-marivo-analysis-dsl-architecture-design.md)。

用户确认 R0/R1/R2/R3/R4/R5 任务完成，以此启动 R6 规划。起草分支 `panda`，HEAD 为
`ea787d116b6c76bb905a3839ad8e0390ec1b239b`；工作树已有 R5.7 未提交代码、测试、文档及新增文件。
[验收主记录](2026-09-26-marivo-full-refactor-acceptance.md#r57-completed-qualification-2026-09-30)
与 [R5 migration ledger](2026-09-28-marivo-full-algebra-dsl-r5-migration-ledger.md)
记载 R5.7 在已接受的方法矩阵内完成。本次读取该记录，不重跑或重新授予 R5 资格。
实施 R6.1 时记录实际 SHA、dirty diff 与新增文件摘要，以最终 R5 交接为准；不清理或覆盖前序改动。

R6 沿用统一 typed graph、方法注册、Runtime 和 Store 7。完成意味着目标方法从公共
Logical/Materialized 入口经过同一执行链，保留定义、域、Cell、端点、参照与充分部件，
并在断源新进程恢复后兑现实际 K。数字相等、私有旧 Dataset 测试通过或接口可导入均不足以完成。

### 1.1 范围与排除

| 能力 | R6 必需交付 | 边界 |
| --- | --- | --- |
| C07 比较与派生量 | TimeChange/CohortContrast/PeriodChange；ExactKeys/UnionKeys；绝对/相对变化、嵌套 Difference；普通 Relation ratio | 公式 linear、时期差、相对差、普通 ratio 和原组件 ratio 不互换 |
| C07 选择 | 类型化多输入谓词、is_defined、严格 where、where→members | 不按列名引入来源，不以行号配对，不以布尔短路豁免检查 |
| C08 cohort | 完整机会域的 any_instance/at_least/all_instances、显式空机会政策 | R7 的 Journey/Interval/Anchor 生产者另行验收；R6 用完整 Entity×Time 闭合公共路径 |
| C08 参照 | share_of、penetration_in、reference_weights、standardize | 不增加任意分组参照 join 或通用分配 API |
| C08 展示 | rank 的四种 ties、partition_by、limit、具名 views；完整同键 terminal table | limit 不是每组 Top-K；table 不获得分析回灌能力 |
| C09 归因 | additive_difference、component_mix，joint/hierarchy，共同 Top-K/Other，核对及筛选后 scope | distinct_membership/distribution_shapley 首次不准入；漏斗归因归 R7；通用 FormulaBasis 不在范围内 |

`ms.statistical_weight`、`mv.statistical_weight`、其命名角色及依赖它的当前行 weighted_mean
已由用户撤回，不能因总计划 C08 的历史文字重新激活，也不是 R6 阻塞项。
独立 reference_weights/standardize 与 Metric/runtime_metric weighted_mean 不受此撤回影响。
R5.7 未授予 Decimal/Duration 当前行 sum/mean/min/max 资格；R6 不隐式扩大该矩阵。

本阶段以真实 DuckDB table/Parquet 来源和 artifact_python 固定续算建立方法资格。
每方法明确数值类型、时间形状、实际路线及正负例，不继承“R5 完成即所有 R6 类型可执行”。
六后端全面资格归 R9，真实 Agent 全量验收归 R10；本计划不授权发布、外部操作或提交。

### 1.2 唯一契约 owner

| Owner | R6 接受的增量 |
| --- | --- |
| [Python Analysis design](../../specs/analysis/python-analysis-design.md) | 具体公开变体、签名、比较设计、对应、参照权重、source/fixed 与 table 边界 |
| [Operators and frames](../../specs/analysis/operators-and-frames.md) | 每方法规则、Cell 消费、量定义、部件、数值状态、K、排名与归因算法 |
| [Session/Runtime](../../specs/analysis/session-state-and-runtime.md) | 有序输入身份、共享实现、交换、receipt、发布与冷恢复 |
| [Timezone/calendar](../../specs/analysis/timezone-and-calendar-design.md) | PeriodChange 桶绑定及时间权威；不重新定义 R5 时间语义 |
| [Semantic object model](../../specs/semantic/semantic-object-model.md) | 被消费的量、单位、角色、关系和组件声明；不写入一次分析的执行事实 |
| [验收主记录](2026-09-26-marivo-full-refactor-acceptance.md) | 方法资格、证据、失败/skip、删除及 R7–R10 交接 |

[旧 typed operators 设计](2026-09-01-lazy-analysis-typed-operators-design.md#exact-attribution-arithmetic)
提供已接受的归因算术、Top-K 和核对规则；旧 Dataset 形状及 codec 不是保留要求。
R6.1 将适用规则归入当前 owner，显式处理冲突；本计划不另造公共注册表或执行资格来源。

## 2. 必须保持的语义与执行约束

### 2.1 比较与普通比值

- 默认 compare 为同量模板、同显式目标输入的 current−baseline 时间变化；时间以外的
  定义、角色、分组、单位和政策必须相容。相同 Entity、列名、行数或两次相同定义的读取
  不足以证明共享目标实现。两期贡献读取也不因此获得共同事务快照承诺。
- TimeChange、CohortContrast、PeriodChange 是闭合设计。CohortContrast 使用共同组坐标
  或 Singleton；PeriodChange 的 window_bucket 在相同非时间坐标内配对完整有序桶，
  桶数须相等，保留原端点和对应；不按筛选后的剩余行重编号、截断或猜测日历映射。
- ExactKeys 要求同一有类型完整键、两侧单射且键像相等，双空合法。UnionKeys 也检查
  各侧唯一性；missing=keep 保存 MissingCoordinate 并产生 Undefined(missing_side)。
  它与 Present(Null/Undefined/Unknown) 分开保存。
- metric_empty 只适用于可证明完整观察中的空贡献，应用具体 Metric 的空状态与 finish，
  不统一填零；筛选、排名、缺版本、未知覆盖和失败不能成为空贡献证据。已存在的非 Defined
  端点不能被覆盖。matched 数值端点必须 Defined 且有限；缺侧得到 Undefined 空结果时保留原因。
- relative_change=(current−baseline)/abs(baseline)，零基线为 Undefined(zero_baseline)。
  绝对差、相对差分别保存单位、定义及政策；嵌套 Difference 保留每层有序端点，
  不把独立 July 捕获当成同一实现，不自动重排为线性公式。
- 普通 `NumericRelation.ratio` 消费 ExactKeys 或绑定精确左右节点的 OneToOneCorrespondence；
  后者消费声明的一对一关系及必要的显式时间对应。禁止 many-to-one、隐式 UnionKeys 或
  metric_empty。缺键像拒绝；已配对实例携带显式 MissingCoordinate 时保留 missing_side。
  matched 操作数须满足有限 Defined、范围及单位条件，零分母为 Undefined(zero_denominator)。
- 普通 ratio 和 Difference 不因保存端点组件就获得原 Metric rollup；嵌套比较也不自动得到归因。
  每项能力按量定义与实际部件独立推导，不按结果类名放行。

### 2.2 多输入谓词、选择与 cohort

where 实际引用的全部输入经显式依赖及完整键对应进入同一消费域。普通数值比较要求
Defined、有限且类型/单位相容；任一失败拒绝整次选择。all_of/any_of/not_ 不短路检查。
is_defined 是标签检查，不能替另一子条件免除消费前提：
`all_of(value.is_defined(), value.gt(0))` 遇非 Defined 仍失败；先 is_defined 选择再比较可成立。
分类、布尔及时间谓词按各自方法规则，不强制套用数值逻辑。

where 保留量定义并精确限制部件；members 只投影身份与选择依据，不重新读取或选人。
非 Entity 的主体集合像不改变原实例域重数；R6 提供 SubjectBinding 消费契约，R7 验证领域映射。

cohort 从保留的完整机会域取得机会和主体映射，不能用有事实的行反推机会全集。
缺机会/缺键/缺覆盖是错误；机会存在但值为 Unknown 才可进入三值量化。
普通数值谓词的 Null/Undefined 为硬失败，is_defined 对这些标签返回 false。
组合前检查全部子条件；false AND unknown、true OR unknown 可决定，但不能掩盖硬失败。

令每主体完整机会域计数为 t/u/f：any_instance 在 t>0 时真、t=u=0 时假，其余未知；
at_least(k) 要求正整数，在 t≥k 时真、t+u<k 时假，其余未知；all_instances 非空时
f>0 为假、f=u=0 为真，其余未知。all_instances 的空域必须显式传入
empty_opportunity.true/false/undefined；与 Metric 空贡献政策严格分型。
输出精确 AnalysisDomain 前，全部目标主体资格必须可决定；unknown/undefined 不能静默排除。

### 2.3 固定参照、标准化、排名与 table

share_of 的默认参照是同计量 Singleton，检查支持包含、相容可加计量或获准分配侧项。
完整份额分区另需证明，非负与正分母成立才承诺 [0,1]。零分母 Undefined；普通 ratio
不能冒充 share。penetration_in 定义为 |B∩Ω|/|Ω|，消费完整固定成员参照；空 Ω 为
Undefined(empty_reference)，重叠类别渗透率之和可以超过一。

固定参照可以是 Logical；其节点及本次实现不会因下游 where/rank/limit 重新选择。
`reference_weights(values, strata=..., unit=...)` 绑定完整唯一层元组、统计单位及输入。
standardize 要求精确分层对应、有限非负权重且和为一，每个正权重层有合法值；缺层、
重复层、错误总和或单位不符均拒绝，不补层或重归一化。输出是新量，不是实际总体值。
权重和的浮点容差、零权重层的消费规则及输出类型必须在 R6.1 当前 owner 中闭合后实现。

rank 保留 values/ranks 同键视图及原排名域。ordinal 按规范实例键打破平局，dense/min/max
使用各自名次规则；partition_by 是有明确对应的 CategoryRelation。非 Defined 行保留
原标签，排名保留相应状态并置尾，不当成零或已知落后。limit 只取全局有序前缀，范围
1–100000 且排除 bool；每组前 k 名通过显式过滤已定义 ranks 表达。where/limit 同步
限制 views，但不重算名次或 share 分母；ties 可以使每组入选超过 k 行。

`mv.table(...)` 仅消费完整同键、同 Session 且来源模式相容的 Relations，列名只作展示标签。
结果有 bounded repr/show 和终端导出，不提供动态属性、字符串列回灌或另一套分组/算术。
同一执行共享显式依赖；独立来源不会因此获得共同事务快照。重复键、错键、跨 Session
和 mixed 输入必须按相应静态/动态边界拒绝，不能退化为外连接或按行拼接。

### 2.4 归因

`change.attribute(axes=..., mode=..., top_k=...)` 从量与充分部件选择唯一方法；内部保留
原 target/basis/rule，无需让单量用户重复提供可推导参数。

| 方法 | 必需依据 | 算术与输出 |
| --- | --- | --- |
| additive_difference | 每侧完整可加分区或明确可加分配，原端点与范围 | 每项 C_i−B_i |
| component_mix | 每侧可加 N、W 与完整分区，目标量和组件政策 | 每侧 N_i/W_total；贡献为两侧分配项之差，不是分组比率之差 |

端点不匹配、缺状态、非法 fold、未知覆盖或零/非法总分母按方法拒绝，不能用 residual
接近零补资格。distinct/quantile 展示值没有其所需 membership/distribution 状态，仍拒绝。

joint 使用完整有序轴元组；hierarchy 使用作者轴顺序的每个前缀，一个轴的 hierarchy 拒绝。
各 resolution 独立核对，不能混加多层贡献。top_k 为 1–1000 的整数且排除 bool；在算术前
对两侧共同 basis 选一次：additive 用 |C_i|+|B_i|，component_mix 用 |W_i,C|+|W_i,B|，
同分按有类型坐标排序。多轴按作者顺序在每个已映射父项内选取，包括 Other；余项形成
真实 typed Other 及 mask，不能用普通分类字符串冒充 Other，也不能分别对两期选 Top-K。

当前 typed-operators 核对阈值为
`abs(D-S) <= max(1e-12, 1e-9 * max(abs(D), abs(S), 1))`。
R6.1 要将它与 R5 各数值类型政策一起冻结：整数/Decimal 的精确状态不得先转 float；
若新增类型需要调整误差规则，先在 owner 接受，不能由实现自行放宽。

AttributionResult 的 contribution/current/baseline 是同键具名数值视图；current/baseline
披露为该方法分配侧项。where 同步限制 views 并保留原核对的 scope，同时撤销当前子域
完整分区承诺，即使筛选后巧合仍和为 target。Logical axes 展开必须来自保留的观察表达式，
成为可见依赖；Materialized 只消费 retained parts，不由 lineage 回源补轴。

### 2.5 执行、数值与恢复

扩展现有 core/rules、method registry、GraphPlan/LoweredPlan 与 Runtime，不新增家族
执行器。构造/计划零业务行读取；必要的 schema-only 边界沿用 R1。静态非法、跨 Session
和 source/fixed mixed 输入在业务读及 Run 前拒绝；键像、覆盖、权重等动态义务由执行履行。
所有受治理准备和校验均由 Ibis 表达；事先注册的本地方法只消费受控交换，不读取 Catalog
或自行开源连接。路线执行失败不再选路，不以 md.raw_sql 补资格。

每方法冻结 int64/float64/Decimal/Duration 的接受或拒绝、单位、精度、溢出、非有限值及
空状态矩阵；继承 R5 物理类型事实，不继承未验证的 R6 算术资格。至少闭合各必需方法的
可执行正例，不能把全部关键格写成拒绝后宣布完成。

source 顶层执行取得新求值；显式共享目标/参照只实现一次。fixed 绑定精确有序 Artifact、
receipt、方法/状态版本及部件，先校验再命中；恢复不加载当前 Semantic 修补冻结定义。
fixed-only 使用受控 Arrow/Parquet→pandas，禁用 DuckDB/远端仍可完成承诺续算。
主表、端点、缺侧、机会覆盖、参照、排名及归因 scope 使用共同发布协议。缺失、损坏、
交换错位和版本不符拒绝；沿用 Store 7，不能仅因 R6 新增字段就另开代际或旧格式双读。

## 3. 分包顺序与出口

每包均包含契约、实现、消费者、披露、针对测试和删除；不能把所有公开文档及部件验证
拖到最终包。下列是工作责任，不要求建立同名模块。

| 包 | 工作与当前定位入口 | 可验证出口 |
| --- | --- | --- |
| R6.1 契约与迁移清单冻结 | 核对 public_dsl/core/predicates、methods、graph 编译/Runtime 与旧 operators/compiler/codecs；在 owner 冻结签名、规则、数值/状态/资格矩阵 | C07–C09 每项有方法、RequiredParts、K、物理路线和测试 owner；范围撤回及前序限制明确；未决方法不得进入后续实施 |
| R6.2 对应与完整比较 | 扩展 typed correspondence、三设计、Exact/Union、缺侧政策、绝对/相对、嵌套 Difference 及普通 ratio | 完整复合键、双空、重复/缺侧、负/零基线、桶映射、单位、独立捕获反例；source/fixed 数值和端点一致 |
| R6.3 谓词与全机会 cohort | 类型化多输入依赖、严格 where、标签谓词、组合逻辑、量词及空机会政策；复用成员投影 | 先过滤与合取的非等价反例；完整 Entity×Time 公共 cohort；Unknown 可决定与硬失败、未知资格拒绝；K/主体映射保留 |
| R6.4 固定参照与标准化 | share/penetration、reference_weights、standardize；绑定参照输入与分层状态 | 筛选前后参照身份/分母不变；缺层不归一、错单位拒绝；普通 ratio 无 share/rollup 升级；断源参照可用 |
| R6.5 排名与终端展示 | 四 ties、partition_by、values/ranks、where/limit、table | 稳定平局、非 Defined 状态、全局前缀和每组筛选区分；table 完整同键及终端边界，共享节点实现计数 |
| R6.6 归因与部件运输 | additive/component_mix、显式轴展开、joint/hierarchy、共同 Top-K/Other、核对及具名 views | 独立 raw-fact oracle、两侧不对称 Top-K、Other 碰撞、每层核对；筛选撤销完整性；固定缺轴不回源；AN11/AN12 SQL 路线关闭 |
| R6.7 冷恢复、消费者删除与安装收口 | 所有新方法 receipt/codec/K，旧 compare/attribute 注册/执行消费者，A02/A06–A08 及 J1–J4 回归 | §5 验收矩阵闭合，同一 wheel 公共旅程和 fresh-process 断源通过；剩余 R7–R9 消费者精确交接，无第二条 R6 执行链 |

依赖顺序为 R6.1→R6.2→R6.3→R6.4→R6.5→R6.6→R6.7。
R6.1 特别闭合 comparison 设计兼容矩阵、嵌套定义模板、state predicate 类型、standardize
浮点/零权重政策、排名顺序、table 输出协议、归因数值矩阵和状态版本；不预建空类型或
先发布无法执行的公共 target。必要的 owning-spec 决策完成前，对应工作包不算可实施。

## 4. 迁移与删除清单

R6.1 新建受版本控制的 R6 migration ledger，逐项登记真实 import/call/registration、
公共可达性、替代 owner、反例、删除条件与残留归属；以下是起始定位，不是已完成调用图审计。

| 当前入口 | 迁移要求 |
| --- | --- |
| public_dsl.py、core/{model,graph,rules,predicates}.py、methods/{semantics,builtin,local,registry}.py | 扩展唯一图与注册；保留 R5 状态 owner，移除被新资格替代的局部限制，不并建 DSL |
| compiler/{graph_plan,graph_lowering,predicates}.py；materialization/graph_* | 接入精确输入角色、动态义务、Ibis/local route、交换和共同协议；不增加后端名分支 |
| operators/{compare,delta,contracts}.py、compiler/comparison.py、comparison_codec/publication | 提取仍有效的算术和边界并迁移公共消费者；删除 R6 旧 Delta 节点/注册/codec 路线，不留 import shim |
| operators/{attribute,attribute_expansion,attribute_values,attribution,attribution_contracts}.py、compiler/attribution.py、attribution_codec/publication | 迁移方法、轴依赖、分配、Other 和核对；不将旧专属 publication 包装成新 Runtime |
| compiler/{distinct_attribution,distribution_attribution}.py | 反查实际消费者；首次仍无公开资格，不为旧测试恢复已撤回能力；共享依赖按 R7/R8 归属处理 |
| observation/{population,metric,aggregation,coordinates}.py、fold_contracts.py | R5 ledger M05/M06 的旧 compare/attribute 消费者随本阶段迁出；有 R7/R8 消费者的共享代码不整目录删除 |
| operators/registry.py、compiler/source_admission.py、materialization/dataset_execution.py | 逐项删除已迁 R6 注册及旧分派；R7/R8 阻断不解除，不以默认准入绕过资格 |
| 旧 comparison/attribution Runtime 测试与 worker | 提取独立 oracle、数值及错误反例，改走公共统一图；不靠删测试或长期 skip 消除失败 |
| Native Help、_capabilities、CLI、site latest 英中版 | 同包更新新入口、精确状态和修复；旧 Help 不重定向，snapshot/reachability/budget 与示例一致 |

R0 SQL ledger 的 AN11–AN12 归因校验随 R6.6 实际提交审计，不能仅凭旧来源路线 blocked
就记为删除。静态 SQL 扫描和驱动提交证据互补；远端资格归 R9 不授权保留新的手写查询路径。
Event comparison/attribution、领域 codecs 归 R7，association/forecast 归 R8；以真实依赖
而非文件名划界。产品兼容链不能以“共享 helper”名义继续开放。

packaged marivo-semantic/marivo-analysis skills 的修改须另获明确用户批准；本次不编辑。
实施先核对既有工作流是否仍适用，若无需修改记录理由；若确需改动，先准备具体差异再按
AGENTS.md 请求批准，不能将未批准的必要披露更新标为完成。AGENTS.md 本身不在修改范围。

## 5. 验收矩阵与独立反例

每个 V 项均关联具体公共方法、签名/类型、source/fixed 路线、测试、证据与状态。
同一 fixture 可以共享，oracle 不调用产品 evaluator 或从产品输出反推预期。

| ID | 必需正例 | 必需反例/判别 |
| --- | --- | --- |
| V01 对应与设计 | 复合键、组/Singleton、双空、三种设计、完整期间桶 | 同行数错键、重复键、错角色、独立目标捕获、桶数不等或筛选后重编号 |
| V02 缺侧与 Cell | Union keep、合法 Metric 空状态、四 Cell 与 MissingCoordinate 独立保存 | where 导致缺行冒充空贡献；matched 非 Defined；Null 被填零；未知覆盖 |
| V03 算术与嵌套 | 负基线相对差、零基线、嵌套 Difference、同键及声明一对一 ratio | 非有限、溢出、单位/时间不符、many-to-one、普通 ratio 冒充原组件/份额 |
| V04 选择与 L1 | 多量/分类输入、标签检查、共同全定义域连续选择与合取等价 | is_defined 合取不保护非法比较；错域/缺键；短路隐藏失败；选择丢端点 |
| V05 cohort | at_least 三真一未知、已可决定假、any/all、三种显式空政策 | 三真一 Undefined 仍失败；两真一假一未知拒绝精确输出；缺机会不能制造 Unknown |
| V06 固定参照 | share 总参照、penetration 交集、非空/空 Ω、权重标准化 | 下游 Top-K 重算分母；缺层重归一；重复层、负/非有限权重、错单位；重叠渗透率被强制总和一 |
| V07 排名展示 | 四 ties、规范键顺序、分区、非 Defined 置尾、同键 table | limit 与每组 Top-K 混淆；筛选重排名；bool/越界 limit；table 外连接/回灌；view 错位 |
| V08 归因 | additive/component_mix、joint/hierarchy、共同 Top-K/Other、侧项及每层核对 | 两期分别 Top-K；把分组 ratio 当侧项；真分类名 Other 碰撞；多层混加；distinct/quantile 无状态准入 |
| V09 scope 与轴 | Logical 展开为显式依赖、Materialized retained 轴、筛选后原核对标记 | lineage 回源补轴；residual=0 伪造覆盖；筛选后仍宣称完整分区；无观察定义的派生量擅自展开 |
| V10 Runtime/身份 | source 新求值、共同节点读取/实现计数、固定命中、不同参照不碰撞 | mixed/跨 Session 早拒绝；执行失败改道；有序 current/baseline/参照交换后误命中 |
| V11 交换/恢复与 L6 | 新进程断源恢复每项承诺 K，并实际调用后续操作；值/定义/部件分别比较 | 删除/损坏端点、机会/参照、排名/归因 parts，错 receipt/版本/schema/键；禁 DuckDB；篡改命中前拒绝 |
| V12 工程/用户 | strict typing 正负例、export snapshot、Help 路由/预算、show/contract/repair、安装公开旅程 | 旧入口/旧执行链仍可达；静态检查冒充 Runtime；来源/身份/秘密泄漏；旧 Help 兼容重定向 |

L1 只在共同全定义原域成立；不得用合法分步过滤证明任意合并合法。L6 必须执行恢复后的
续算，不只比较 contract 字符串或 Artifact ID。共享 R5 规则受影响时补 L8 原状态与 L9
同目标含空组回归；不将普通比值、标准化量或归因子域升级为原 Metric 归约。

公共旅程以总计划 A02（比较选人再观察）、A06（分支/期间/嵌套）、A07（固定参照/排名/
标准化/归因）、A08（全机会 cohort）为本阶段端到端必需集，J1–J4 作基础回归。
A06 复用已获资格的 R5 时间/fold，不把未获资格的后端或类型计为本阶段通过。
每旅程包含独立预期、source 变化、固定输入保持和 fresh-process 恢复；A07 必须同时
检验参照身份及筛选后核对 scope，A08 同时包含可决定 Unknown 和硬失败。

## 6. 工程门禁、证据与完成判定

日常按工作包运行最窄的 `make test TESTS='...'`、`make runtime-test TESTS='...'`、
`make typecheck TYPECHECK_TARGETS='...'` 和 `make lint-agent LINT_TARGETS='...'`。
新增/修改测试先使用仓库 marivo-test-fixtures skill；现有 tests/test_lazy_compare_*、
test_lazy_attribute_*、test_lazy_attribution_* 用于提取业务回归，新文件名在 R6.1 ledger
中落实，不在计划中把未创建的测试命令写成可执行事实。

公共/共享改动收口跑 `make check-agent`、必要定向 Runtime 和 `npm --prefix site run build`，
最后 `git diff --check`。default tests 的 skip 不计 Runtime 通过。故障注入验证发布原子性、
失败清理及无重放；涉及共同协议时覆盖损坏恢复与完整资源关闭。普通阶段不运行完整
release-check、不启动 MinIO；本机定向实源测试不代替 R9 六后端验收。

R6.7 构建同一非 editable 候选 wheel，记录 wheel/source/test/依赖摘要；在隔离环境核验
每进程 installed origin，运行公共旅程和状态测试。produce/continue/recover 使用该同一
wheel；恢复进程屏蔽源项目/Semantic/连接并禁用 DuckDB，确保不偷读 checkout 或重算。
有修复就重新构建并验证最终候选，不拼接不同候选的通过记录。

R6 migration ledger 与验收主记录保存：基线与 dirty 摘要、命令/环境/退出码、方法版本、
实际数值与来源资格、每个 V 项结果、失败与 skip 理由/恢复条件、旧消费者处理、证据路径。
原始附件若在 ignored 目录，受版本控制的索引提供摘要及复现方式；不可取得的历史附件
只作历史记录，必要门禁重跑。planned/static/passed/failed/blocked/unverified 分开登记。

R6 完成须同时满足：

1. 本轮接受的 C07–C09 方法全部有公共执行正例、必要反例与明确资格；撤回项明确排除，
   必需缺口没有以“未来扩展”或全拒绝矩阵绕过。
2. 三设计、缺侧/Cell、普通比值、多输入选择、完整机会、固定参照、排名展示和两类归因
   均经统一图/Runtime/Store；值、语义、输入身份和 K 分别通过验证。
3. V01–V12、A02/A06–A08、J1–J4 所需回归及同一 wheel 冷恢复完成；失败/skip 有明确处置，
   没有以私有测试替代公共资格。
4. R6 旧注册、执行、SQL 校验及专属 codec 消费链退出；共享残留按真实 R7/R8/R9 owner
   交接，不能回退或恢复旧状态。披露、类型、当前英中示例同步，必要 skill 变更已获批准。
5. R7 得到主体/机会映射、领域 comparison/attribute 接入契约；R8 得到排名/数值视图和
   固定结果续算规则；R9 得到方法×类型×时间×来源形态×路线矩阵；R10 得到可复现安装旅程。

R6.1 已完成当前 owner 的契约冻结与静态迁移盘点；这不授予新增方法执行资格，
也不表示上述 R6 全阶段完成条件已通过。R6.2 的实现、公共证据、跳过和未验证项已记录于 ledger；
R6.3 与 R6.4 的公共执行、消费反例与固定恢复证据已记录于 ledger；R6.5 的排名与终端展示、R6.6 的归因与部件运输见 ledger；R6.7 继续按其逐项出口实施。
