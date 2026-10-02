# Marivo 全量分析代数与 Analysis DSL：R7 实施文档

Date: 2026-10-01

Status: R7.1 documentation/static contract freeze complete; R7.2 private preparation implemented;
R7.3 public Journey integration implemented with bounded evidence below; R7.4 funnel and Findings implementation is recorded below; R7.5 canonical History is recorded below; R7.6-R7.9 remain unimplemented. Qualification is limited to each phase's recorded evidence.

The [R7.1 migration ledger](2026-10-01-marivo-full-algebra-dsl-r7-migration-ledger.md),
[consumer snapshot](2026-10-01-marivo-r71-consumer-snapshot.json) and
[evidence index](2026-10-01-marivo-r71-evidence-index.md) bind the completed freeze
to its actual baseline, sole owners, consumers and mandatory planned requirements.

## 1. 目标、前置交接与文档权威

执行[总实施计划 R7](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md#r7--eventlifecycle-与-anchor-领域方法接入)，
完成 C11–C13 和 C18：Event matching、漏斗及其比较/归因、步骤耗时与领域选人、Lifecycle
replay 与领域视图，以及相对 Anchor 观察和 retention。全部接入既有 typed graph、唯一
method registry、Runtime、受控交换与 Store 7，不保留另一条领域 Dataset 执行链。

用户确认 R0/R1/R2/R3/R4/R5/R6 已完成，以此启动 R7 规划。起草分支 `panda`，HEAD 为
`b8615244129d0f7e83995255c588545e243840b1`，起草前跟踪工作树干净。
[验收主记录](2026-09-26-marivo-full-refactor-acceptance.md#r67-and-full-frozen-r6-completed-acceptance-2026-10-01)、
[R6 migration ledger](2026-09-30-marivo-full-algebra-dsl-r6-migration-ledger.md)和
[R6.7 evidence index](2026-10-01-marivo-r67-evidence-index.md)是当前交接依据。
本次读取已有验收，不重跑、不扩大前序资格；实施 R7.1 时重新记录实际 SHA、dirty diff
及输入摘要，不清理或覆盖前序和无关改动。

R7 完成必须同时证明值、领域定义、完整实例域、轨迹/assignment、部件、输入身份和
可执行 K。数字相等、类可导入、旧私有 harness 通过或只验证最终状态均不足以完成。
历史文档中 Population/Dataset、select_subjects、源 SQL bundle、旧 codec 和身份排序的
规则冲突，按当前接受契约逐项处理，不能因旧测试仍存在而恢复。

### 1.1 范围与前序边界

| 能力 | R7 必需交付 | 独立边界 |
| --- | --- | --- |
| C11.a matching | exact participant pattern；first_per_subject；every_start 的 exclusive/shared；canonical Journey assignment、顺序和覆盖 | 不增加任意 matcher、最大匹配策略或通用排列证明器 |
| C11.b funnel | 同一 assignment 的固定计数/率、领域 compare、funnel_ratio_mix 归因、单量 read | every_start 不自动获得主体漏斗；不重建通用 Delta/Attribution Dataset 家族 |
| C12 耗时与选人 | exact 步骤对、completed/observed duration、dropout 真值、SubjectBinding、where→members；领域机会接入 cohort | Journey 重数不变成 Subject 重数；absence 不自动成为失败机会 |
| C13.a replay | from_inception、业务顺序、canonical history、legal transitions、violations、完整主体分类 | 不增加 projection seed、窗口起点默认状态、任意 conflict callback 或分段 replay API |
| C13.b 视图 | read(in_state)、distribution、transitions、violations、intervals、dwell 及固定续算 | 描述迁移占比不成为马尔可夫概率；dwell 不变成完整生命周期时长 |
| C18.a Anchor | Event role/Journey 起点、Subject×Anchor、elapsed/calendar 相对窗口、Metric/RuntimeMetricExpr 观察 | R0 已接受共享重叠；不新增 exclusive/overlap 开关 |
| C18.b retention | 固定 Ω、K+/K-/K?、确定性界、any_anchor/every_anchor、已知真值选人 | 不删未知、不默认主体去重、不称置信区间、不上卷标量界 |

R5/R6 的 Subject image、完整机会域 cohort、多输入谓词、数值 Relation、排名/table 与
AttributionResult 是复用基础，不是 R7 领域生产者已经获准的证据。R6.7 留下的私有
`funnel_delta`/`funnel_attribution` 只拥有实际 Event 消费者，仍须迁出旧执行、发布和恢复链。

命名 statistical_weight、其当前行 weighted_mean，以及通用 distinct/distribution attribution
仍不在范围内。R5/R6 未授予的 Duration 当前行归约，若为 C12 的
`completed.duration.summarize(mv.mean())` 必需，R7 必须在现有 row-method owner 补足
这条精确资格；不能将该必需路径记为 R8 再宣称 C12 完成，也不连带开放 Decimal 或其他
未接受 reducer。Lifecycle 自有 median/p90 按 dwell 方法验收，不授予通用统计扩展资格。

本阶段必需实源为 DuckDB native table 和 local Parquet，固定路线为 artifact_python。
六后端/远端物理形态全面资格归 R9；真实 Agent 与发布验收归 R10。本计划不授权外部服务
操作、提交或发布。普通阶段不启动 MinIO、不运行完整 release-check。

### 1.2 唯一契约 owner

| Owner | R7 需要接受/补齐的规则 |
| --- | --- |
| [Python Analysis design](../../specs/analysis/python-analysis-design.md) | 公开闭合类型、签名、owned handles/read、领域与 Subject 映射、Logical/Materialized、C18 构造和 K |
| [Operators and frames](../../specs/analysis/operators-and-frames.md) | matching/replay/reducer 方法、Cell、充分部件、量/单位、耗时精度、allocation、parts transport 与条件 K |
| [Semantic object model](../../specs/semantic/semantic-object-model.md#business-order-and-simultaneous-events-r23-declaration-implemented) | R2 已实现的 Event occurrence/participant、StateModel、business_order 与版本/依赖；一次分析的覆盖不写回声明 |
| [Session/Runtime](../../specs/analysis/session-state-and-runtime.md) | 显式依赖、一次实现共享、交换、资源、receipt、原子发布、恢复及求值身份 |
| [Timezone/calendar](../../specs/analysis/timezone-and-calendar-design.md) | occurrence 时间、排他随访、end 左极限、elapsed/calendar、DST 和固定时间依据 |
| [验收主记录](2026-09-26-marivo-full-refactor-acceptance.md) | 逐方法资格、独立证据、失败/skip、删除及 R8–R10 交接 |

目标语言依据为[DSL 接口设计 §8.1–§8.3](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md#8-事件状态与统计扩展如何接入)，
组合与执行依据为[分析代数](2026-09-23-analysis-algebra-theory.md)和
[执行架构](2026-09-24-marivo-analysis-dsl-architecture-design.md)。
[旧 Event/Lifecycle 设计](2026-09-01-lazy-analysis-subject-event-lifecycle-design.md)提供
matching、reach classification、计数、replay、轨迹和 dwell 的业务反例；其旧参数、类型、
SQL、跨 Session 复用、源缓存和 codec 不是兼容要求。

R0 已在[Analysis C18 owner](../../specs/analysis/python-analysis-design.md#relative-anchor-observation-and-retention-c18)
闭合 Anchor/retention；R7.1 只补实现所需精确类型、状态和消费规则，不重新把 C18 判为
“未定研究”。出现冲突先在对应 owner 接受决定，再写产品代码；本计划不成为第二份 API registry。

## 2. 必须保持的领域语义

### 2.1 occurrence、身份、业务顺序与覆盖

- 每条 occurrence 保存 exact Event 定义/版本、完整 occurrence K、时间、participant 到
  完整 Subject K 的受治理映射。各 Event 的 occurrence 身份以 Event 绑定区分，不能把
  不同 Event 的同名 ID 合并；来源重复、空身份、缺 participant、to-many 或版本重叠拒绝。
  同一 Event 在 pattern/model 中重复使用时，共享同一显式输入，不重复读取后再猜测相等。
- 时间先区分不同 instant；同刻只消费 R2 的 sequence/precedence 业务依据。执行核验
  sequence 类型、同 Subject 唯一性、枚举值、participant 和相关定义/来源绑定。
  occurrence ID、Event 名、声明顺序、物理行顺序和稳定排序不能提供业务先后。
- 未完全排序仅准入有封闭规则和独立验证的具体方法，必须证明请求输出以及保留的
  assignment、trace、violation、interval、主体分类与 K 所需部件在允许顺序下不变。
  同一终态而违规 occurrence 身份不同仍拒绝。不开发任意事件/模型的排列搜索或通用证明器；
  方法没有该证明就要求业务顺序，并报告具体修复。
- 覆盖使用现有 `BoundedCompletenessDeclarationV1` 和
  `SourceOriginCompletenessDeclarationV1`，核对 exact Events、datasource、定义版本及边界，
  保留 observed/declared/mixed/unknown。声明是有追溯的来源假设；空结果、count、最大时间、
  rationale 和 Event 声明本身均不能证明完整。缺随访产生领域 Unknown/censoring；
  损坏、错绑定、矛盾或非法覆盖是错误，不能降级成 Unknown。
- 检查必须针对实际被消费的同一实现输入；不能先检查一遍源，再另读变化后的源产生
  assignment/parts。使用已捕获输入或实际获准的快照机制，记录其 authority。多 Event
  输入和多个编译查询不自动获得共同事务快照，历史单 statement bundle 的一致性义务
  不能在拆分后静默丢失；必要物理保证缺失时精确路线阻塞。

### 2.2 canonical Journey、步骤耗时与 dropout

唯一入口仍为 `session.events.match(pattern, population=members, cohort_window=...,
completion_through=..., matching=..., business_order=..., completeness=...)`；`population` 使用新 AnalysisDomain，
不接受旧 PopulationInput 或 Dataset 投影模式。成员时间、开始窗口与随访独立。
开始窗口为半开区间；`completion_through` 是排他绝对上界，不是每个开始的相对窗口。

pattern 是非空有序 exact PatternStep，所有 participant 指向同一 Subject；重复 Event
可以出现在不同步骤。first_per_subject 只选每主体最早合格开始；every_start 为每开始
保留一条 Journey。每次尝试按步骤选前一步之后最早合格 occurrence；一次尝试中一个
occurrence 不填两步，缺中间步骤不能跳过。every_start 的中间 occurrence 可跨尝试复用；
shared 最终 occurrence 可完成多个尝试，exclusive 逐最终 occurrence 分给最早合格未完成
尝试，最终步骤保留与中间步骤复用分别处理。禁止用 greedy/max-cardinality 替换该算法。

Journey 身份绑定 Subject、开始 occurrence、pattern/matching 及此次实现。没有开始
occurrence 的主体没有自动补出的失败 Journey。canonical assignment 与逐步 reach 真值
保存为共同部件；funnel、duration、dropout 和 Anchor 起点均消费它，不重新 matching。
首个缺步骤为 Unknown 时后续不能靠后一步覆盖变成已知失败；已证明缺步骤时后续不可达。

`time_to_event(from_step=..., to_step=...)` 要求同一保留 pattern 的 exact 有序步骤，可跨
中间步骤但不能绕过原 assignment。EventDurationResult 保留完整 Journey 域及固定关系：
status、started_at、completed_at、duration、observed_duration、followup_until。
完成、已进入未完成、coverage-censored、not-entered 和 entry-unknown 分别编码，不能用
物理 NULL 合并。duration 只属已完成步骤对；observed_duration 为有依据的已观察随访，
不取“最后事件时间”，不冒充完成时间。两者是 elapsed Duration，不默认工作时间/日历日。

`completed()` 选择已知完成子域，返回 CompletedJourneys。耗时 Relation 保留步骤对与
Journey 单位；u1 两次 10/30 秒、u2 一次 100 秒，旅程均值为 140/3 秒，主体均值再平均
为 60 秒。耗时大于 15 秒再 members 得 u1/u2，但不能拿两主体数替三 Journey 分母。

`journeys.read(mv.dropped_before(step=...))` 是 first_per_subject 的 BooleanRelation：
绑定已开始机会、前序 reach、最终 assignment、随访和覆盖。未观察完不是 False；
普通 where 在 Unknown 上拒绝完整选人。`subjects(role)` 返回 exact Journey/Pair 域的
SubjectBinding，where 运输子域映射，members 只取主体集合像，不再读取/选人。
every_start 的“存在/至少 k 次/全部失败”须有完整机会及已接受真值方法，再进入共同 cohort；
不得删除 first_per_subject 限制来开放同义 select_subjects。

### 2.3 funnel、领域比较、归因与单量 read

`journeys.funnel(axes=())` 返回 FunnelResult，只准入 first_per_subject。非时间 Dimension
axes 在第一 occurrence 时点取值，保存 exact 版本、受治理路径和完整分区，不能改用
当前属性。无 axes 的空 Journey 为每步骤产生合法零计数；有 axes 只对实际完整元组
产生 dense steps，不发明空地区或 Cartesian 分组。Null axis 为明确类别，不是缺主体身份。

保留现有 cohort/resolved-cohort、entry/resolved-entry、reached/lost、coverage-censored
计数和三种率；率的 first/previous 角色与 resolved 分母明确。各目标步骤的 reach 和
覆盖按同一 assignment 判定，计数 exact int64 并检查溢出；率由组件 finish，零分母为
Undefined，不能求平均或把 Unknown 计为已知流失。分组组件必须精确核对未分组目标。

`funnel.compare(baseline)` 返回 FunnelComparisonResult。funnel-period 规则检查 pattern、
matching、Subject、显式成员定义、Event/axes 定义一致，时间域、开始窗口长度和结束后
随访长度相容，要求相关后续观察完整。两侧步骤与组坐标完整 outer 配对；缺侧只有在
证明完整且确无该组时才给计数零，零分母率仍 Undefined。不能将此领域规则搬到普通
NumericRelation.compare 作为默认补零。

`change.attribute(target=mv.funnel_loss_rate(step=...), axes=..., mode=..., top_k=...)`
使用唯一 `funnel_ratio_mix@v1`，输出既有公共 AttributionResult。目标绑定 exact 非初始
步骤及 lost/resolved-entry 两侧组件，保留 loss 与 denominator_mix 分解，不替换为
普通分组率之差或 R6 component_mix 的另一公式。令两侧 lost 为 L、resolved-entry 为 E，
目标为 `L_current/E_current - L_baseline/E_baseline`；每完整 basis 项的 loss 为
`(L_i,current - L_i,baseline)/E_current`，denominator_mix 为
`L_i,baseline * (1/E_current - 1/E_baseline)`。两侧总 E 必须正；两类贡献合计复现目标，
分配侧项另按原方法定义复现两侧率，不将贡献项当各组实际率。
R7.1 将这些公式归入当前 owner，并固定侧项含义、共同 Top-K 评分、
typed Other/mask、层级每个 resolution、零总差与核对精度，复用 R6 的 scope/排名协议。
独立精确分数 oracle 同时核对贡献、侧项和目标；residual 接近零不证明完整。

Logical 缺 axes 时，图显式加“同一次 assignment 的维度分解”依赖并复核原目标。
Materialized 缺轴或组件不能从 lineage 回源；保留完整 assignment 的 Journey 可在其
实际 K 下重新构造 funnel。fixed 缺历史维度值时拒绝，不能另读 Semantic/源来补。
筛选贡献只限制 views，保留原核对 scope 并撤销子域完整分区承诺。

领域单量统一用 `read`，现有 `funnel_loss_rate` 和原 owned 字段句柄具有精确重载，返回
具体 NumericRelation 并保留步骤、条件分母、覆盖、原组件/比较端点。R7.1 固定原字段的
typed handles，不增加 counts()/values()/loss_rate() 同义链或字符串列 lookup。
普通单量视图不因能比较/展示就获得完整漏斗比较、主体映射、归因或率上卷能力。

### 2.4 canonical History 与领域视图

`session.lifecycle.replay(model, population=members, window=..., seed=mv.from_inception(),
completeness=...)` 消费模型 exact 主体。seed 必填且仅 from_inception；真实 inception
可以早于输出窗口，window 只裁剪报告范围。bounded coverage、最早事件或窗口起点不能
替 source-origin 依据，也不取最近状态、initial 默认值或另一个来源作 seed。

replay 每 distinct trigger 的一次显式输入，按已接受业务顺序扫描。合法触发保留 legal
transition，包括自循环和零时长迁移；非法触发按现有 record_and_continue 保持状态并
记录 occurrence 违规，terminal state 后触发保留专门违规。未进入模型的 Event 不读取。
前序起源不足保留 Unknown；完整历史有 modeled trigger 却缺必需 inception 原子失败。
完整 source-origin 历史证明从未进入模型才为 NotStarted，不产生 initial 区间。

canonical retained state 必须分开保存：裁剪区间、全部合法迁移、违规 occurrence、每个
输入主体的 inception/known-prefix/coverage 分类和边界。相同正时长区间不能替代同刻
迁移轨迹；只有区间不能恢复 self-transition 或中间零时长状态。不得将两个 History
作为 bag 相加来 replay，也不新增未闭合的分段续接。

| 入口 | 行单位与规则 | 不可替代的部件 |
| --- | --- | --- |
| read(in_state) | 完整输入 Subject；确定目标状态 True，其他确定状态/NotStarted False，覆盖不足 Unknown；where→members | 每主体分类、known prefix、inception、区间和时点依据；无区间主体也必须保留 |
| distribution(at, axes) | Checkpoint×ModelState×AxisGroup；维度在各 checkpoint 取值；模型 state 域保留零计数 | 主体分类、区间、检查点维度值和精确 seeded 分母；未知/NotStarted 分开披露 |
| transitions() | 模型声明的完整 TransitionPair 域，合法零计数；分母为窗口内全部 modeled transitions | canonical transition trace，包括 self/zero-duration；不由区间变化次数反推 |
| violations() | TriggerOccurrence，固定 trigger/time/state/kind 与实际 SubjectBinding | occurrence 身份和违规 trace；不自动成为 Finding 或因果判断 |
| intervals() | Subject×StateInterval，state/start/end/observed_duration、left-clipped、终止与覆盖状态 | 区间两端、触发和 censoring；where 后映射不重放 |
| dwell() | ModelState；interval/completed/right-censored/coverage-censored/left-clipped-completed 计数与 mean/median/p90 | completed-window-fragment 定义与统计所需 interval/精确状态；摘要值不授予原量 rollup |

in_state/distribution 检查点限 `[start,end]`；`at=end` 为排他 end 前左极限，恰好 end
的触发不消费。distribution 的 share_among_seeded 为条件份额，不声称覆盖未知主体的
全体份额。dwell 保持 `completed_window_fragment_duration@v1`：先裁剪，统计窗口中已
结束的片段；left-clipped completed 参与，right/coverage-censored 不参与完成时长。
全观察占用时长需 intervals().observed_duration，完整进出时长需两端完整选择；三者不混同。

所有固定字段进入共同 Relation 协议；主体级 in_state 无需 through，interval/violation
选人必须使用保留映射。ModelState/TransitionPair 汇总不能从计数重建主体。
Duration mean/median/p90 的 tick、单位、插值、舍入、溢出及空状态在 R7.1 当前 owner
冻结；不得先转 float seconds 丢精度，也不得以平均组均值/组 p90 获得整体统计。

### 2.5 Anchor 相对观察与 retention

沿用 R0 的唯一目标入口：

```text
session.anchors(source: ParticipantRoleHandle, *,
                population: AnalysisDomain, during: TimeScope,
                business_order: Ref[BusinessOrderKind] | None = None)
session.anchors(source: JourneyResult, *,
                population: AnalysisDomain, during: TimeScope)
AnchorDomain.observe(metric: Ref[MetricKind] | RuntimeMetricExpr, *,
                     within: ElapsedWindow | CalendarWindow,
                     via: Ref[RelationshipKind] | RootRoutes)
AnchorDomain.retention(returning: ParticipantRoleHandle, *,
                       within: ElapsedWindow | CalendarWindow,
                       completeness=(...))
RetentionResult.by_subject(*, rule: AnyAnchor | EveryAnchor)
```

这些是待实现契约摘要，不是当前可执行示例。R7.1 固定匹配的 Materialized 输入/返回重载
及 source/fixed 分类；不能因 `session.anchors` 名字带 Session 就为固定 Journey 偷加源。
锚点保留完整 `(Subject K, Anchor occurrence K, source definition/version)`，Journey 来源
还绑定原 pattern/matching/assignment。during 只选开始；无 Anchor 的主体不在此 Ω，
不能为“所有客户 retention”补失败 Anchor。固定 Journey 的 population 必须有相容 fixed
身份，否则按 mixed 早拒绝。

`mv.elapsed(mv.duration(hours=168))` 与
`mv.calendar_days(days=7, timezone=ZoneInfo("America/New_York"))` 是不同闭合类型。
二者长度必须正，calendar 绑定 IANA zone。窗口 `[anchor,deadline)` 排除 Anchor occurrence
本身；不同 occurrence 同刻是否更晚须有业务顺序事实。calendar 先按具名 zone 的本地
日历求 deadline，再转 instant；跨 DST 不能降成 168 小时。R7.1 固定非存在/重叠本地时刻
的规则或精确拒绝，不能借主机时区或隐式 fold 修复。

相对观察复用 R5 的 Metric/RuntimeMetricExpr、路径、原组件和 Cell 方法规则，但每 Anchor
单独绑定完整窗口、贡献与覆盖，multi-root 各自准备；不是把所有 Anchor 的最小/最大时间
变成一次总体观察再复制。允许同一 occurrence 在多个重叠窗口共享贡献，保存每个
`(Anchor, occurrence)` 绑定；不因此获得去掉 Anchor 坐标后的原状态 rollup。只有另有
真实不重叠/分配证明与已注册方法才可运输该能力。

retention 在读取返回 Event 前固定 Subject×Anchor Ω。观测到合格返回即 K+，可在部分
随访下成立；没有返回且 exact Event/source/window 完整到排他 deadline 才 K-；其他
情况 K?。三集合互斥并完整覆盖 Ω，保存覆盖与 Anchor→Subject 映射。

```text
nonempty Ω: lower = |K+| / |Ω|; upper = (|K+| + |K?|) / |Ω|
empty Ω:    lower = upper = Undefined(empty_omega)
```

100 个实例 25 真、5 假、70 未知必须为 [25%,95%]；未知不从分母删除。
by_subject 明确建立 Anchor 域的 Subject 像为新 Ω：any_anchor 有真即真、全假才假、其余
未知；every_anchor 有假即假、全真才真、其余未知；没有默认量词，每个像主体至少一 Anchor。
一真一未知与一假一未知必须分别验证两规则，不得把实例率默认去重为主体率。

result 的 status views、show/contract、已知真值选择及 members 保存固定原 Ω 的含义。
选择供查看的子域不能静默重定义已绑定总体或 bounds；新的总体问题须显式构造并记录。
members 要求已选择、可决定的 true Subject 状态；未知/不完整选人拒绝。
不提供 scalar-bound rollup、界的通用算术、drop_unknown 或 K?→False。
Insufficient follow-up 是 K? 而非执行失败；损坏、重复身份和矛盾覆盖仍原子失败。

## 3. 统一执行、状态与披露义务

扩展现有 `core.model/rules/graph`、`methods`、`graph_plan/graph_lowering`、graph Runtime、
`graph_snapshot/graph_publication` 和 Store；领域 matcher/replayer 作为有闭合输入、规则、
状态/部件与资源约束的方法注册，不另造 AST、scheduler、artifact store 或家族 cache。
当前 SourceDefinition 只直接承载 Entity/Metric，领域 Ref、顺序和覆盖要在 R7.1 明确进入
同一 DAG 的 exact 定义/参数及依赖，不能藏入运行 closure 或仅写 lineage。

Prefer qualified source-side Ibis implementations for occurrence, matching,
funnel and other domain calculations; apply member/time restrictions, joins,
checks and sufficient-state reduction at the source to minimize transferred
rows and bytes. Assess efficient backend-native operators, including ClickHouse
parametric sequence/funnel functions, against the complete method contract and
exact server/source-form/precision qualification before selecting them. Native
depth, existence or counts alone cannot replace canonical assignments and parts.
The [R7.1 feasibility evidence](2026-10-01-marivo-r71-evidence-index.md#source-pushdown-and-clickhouse-feasibility)
records candidates and the current supported-Ibis binding gap; actual remote
qualification remains R9.

Select bounded `ibis_python` only for a documented irreducible operation after
source feasibility is assessed. Its Ibis preparation includes all governed
membership, occurrence/participant, historical attributes, time/order and
completeness inputs and uses SourceSession-issued compiled artifacts. Engine
failure cannot trigger another engine or a full-table Python retry. Private
Event visitors, SQL AST/string patching, packet CTEs, recursive macros and
handwritten algorithm/check queries are retired. Existing datasource
metadata/control exceptions keep their exact scope.

本地 matching/replay 选人后的新 Metric 观察，以及 Journey Anchor 的相对观察，采用
同一 source Run 内的“先准备全部 source 依赖，再本地选择/限制与归约”。计划从完整
Logical DAG 收集后续 Metric 组件、路径、时间和历史属性依赖；Ibis 前缀为原输入成员域
及方法要求的时间范围准备受治理的候选贡献与充分状态，本地阶段以实际 Subject image 或
Anchor 窗口精确限制这些输入。候选范围不能依赖尚未产生的本地主体 ID；Journey Anchor
须由开始窗口和 relative window 推导有界准备范围，并保留精确时刻以逐 Anchor 判断。
空选择/空组、scope、历史属性、组件与重叠贡献遵守原方法规则，不能用已按候选总体
聚合的标量替代所需明细/充分状态。无法证明准备范围或资源界时拒绝该路线并阻塞必需格。

R7.1 冻结这条 placement 和交换 schema；R7.2 扩展 `graph_plan/graph_lowering` 与
观察的 preparation/local consumer，R7.3/R7.6/R7.7 分别接通领域生产者。当前
`graph_plan` 对 source route 的 local predecessor 拒绝仍是默认规则，不能仅扩大方法
allowlist 绕过它；只有显式注册并通过资格的上述计划形态可准入。全部源读取都必须可见于
source 前缀，禁止本地结果回传/上传到源、source-after-local 隐藏查询、重选人或
rematch/replay。显式 Materialized members/Journey 加 live source 仍是 mixed，读前
拒绝；先物化再观察不能替代 A09 的同 Run Logical 正例。

构造/计划零业务 I/O；必要 schema-only preflight 沿用 R1。跨 Session、source/fixed
mixed、错 role/pattern/model、已知不合格路线在业务读及 Run 前拒绝；实际键、顺序值、
覆盖/轨迹检查按 consume/publish deadline 履行。下游领域 reducer 复用同一显式
assignment/history；独立构造的 equal definition 不合并实现，多 trigger 不隐藏读取。
source 重执行分配新 Run 并读取当前源；fixed 绑定精确输入与既有执行键，可精确命中。

A source realization enters its selected local stage through controlled
BatchStream/Arrow, preserving algorithm boundaries across batches. The sole
R7 execution budget is the Runtime owner's unified 600-second execute deadline,
shared by every stage and route. Row/byte facts measure transfer efficiency;
occurrence, Subject, step, part-row and memory quotas are removed by the latest
user instruction. Timeout, cancellation, early close, bad batches and consumption
errors close cursor/connection, reader and staging and fail atomically. No
truncation becomes complete data, no unconstrained collection substitutes for
governed preparation, and ambiguous order is not resolved by permutation search.

固定读取只经验证 receipt 的 Arrow/Parquet→pandas/数值核。缺部件拒绝，禁止
Artifact→DuckDB、远端上传、current Semantic load、origin replay 或 lineage 补轴。
冻结 exact schema、键、定义/方法/状态版本、ordered inputs、顺序/时间/覆盖依据和所有
RequiredParts；恢复前及 exact hit 前均核验。沿用当前 Store 7、graph-dag-v1、
continuation/execution-key v2 的 owner；若需协议变更，在 owner 显式版本化并拒绝旧格式，
不双读、不迁移，不未经核验宣称 wire format 未变。

Evidence/Findings 同属这条发布与恢复协议。当前 `graph_store.artifact()` 无条件要求
零 Findings、`digest("[]")` 与空 extractor 版本列表，这只是现有图切片的限制；R7 必须迁入 funnel
compare/attribute 的非空代数 Findings 能力。R7.1 在现有 owner 冻结逐 producer 的
extractor/policy 版本、闭合 body、输入/Artifact 绑定、候选资格、排序与 cap，以及
eligible/emitted/truncated counts；保留旧业务规则与独立 oracle，不沿用旧 Dataset codec。
比较仅取 calculation_status=ok 行，按 abs(loss_rate_delta) 降序、完整 row key 稳定
排序；贡献仅取 reconciled 行，按 abs(contribution) 降序、resolution/row key 排序。
两者沿用 cap=1000；完整 row key 只用于内部排序，不能将身份值投影进 Finding。
match/replay 等无 extractor 的方法按显式 zero-findings policy 验证；compare/attribute
可因无合格行得到空集，但不能把所有新图结果一律清零。领域违规不自动成为 Finding，
Findings 保持 algebraic，归因的 causal_claim=none，不暴露原始 Subject/occurrence 身份。

R7.4 在现有 Store 事务 owner 内原子发布 Artifact、Evidence、Findings 和 terminal
记录；Evidence 的 finding_count、finding_set_digest 与 extractor 版本绑定实际集合。
`graph_publication/graph_store`、Session 读取与公开 `evidence_digest`、`findings()`、
`finding()` 共用该验证规则，覆盖首次读取、冷恢复和 exact hit。body/身份/版本/集合
缺失、损坏或错绑定在返回结果前拒绝；extractor 或事务失败不发布任何部分结果。
协议变更按上段显式版本化，无第二套 Evidence store、旧 codec 双读或静默跳过坏行。

各具体 result 保持 bounded 单行 repr、deterministic show、基于实际部件的 contract 和
具体修复。Help 拥有静态事实，result 拥有当前 K，错误拥有修复；没有第二份 renderer
inventory。公共 exports、精准 typing、原生 Help/预算、CLI、示例和最新英中文档同迁。
packaged skills 若需变更，先完成具体可审阅 diff 方案并单独取得用户明确批准；当前规划
不编辑它们。尚未取得批准的必要 skill 同步不能写成已经完成。

## 4. 分包顺序与可验证出口

主链为 `R7.1 → R7.2 → R7.3 → R7.4 → R7.5 → R7.6 → R7.7 → R7.8 → R7.9`。
共享实现/状态的基础在拥有者阶段落地；每包同步相关 owner、public guidance、英中示例
及定向验证，不把公共契约漂移统一拖到 R7.9。阶段编号不是隐式执行授权。

### R7.1 — 契约冻结与消费者/资格 ledger

建立受版本控制的 R7 migration ledger，固定以下 F 项及本文 M/V 到具体符号和测试节点：

| ID | 必须冻结的决定 |
| --- | --- |
| F01 | 完整 Subject/occurrence/Journey/interval/violation/checkpoint/state/Anchor 键；具体域种类和 public Logical/Materialized 类型，不以 Group 伪装领域实例 |
| F02 | session.events/lifecycle 的 AnalysisDomain 签名与 Session/来源模式；旧可选 PopulationInput/隐式根的退出，fixed 重载精确边界 |
| F03 | business_order 在 Event/Anchor 构造中的唯一绑定入口及 StateModel 默认消费；缺排序时可准入的封闭不变性方法、输出集合、失败/资源界 |
| F04 | matching assignment/reach schema、重复 Event、one-step 与多步、exclusive/shared、输入 sharing 和结果稳定排序 |
| F05 | coverage exact binding、observed/declared/mixed/unknown、known prefix、截止边界和同一实现输入的检查/消费一致性 |
| F06 | duration 状态、completed/observed 差别、tick/插值/舍入/空值；必需 Duration row mean 的 owner 与资格 |
| F07 | funnel owned handles/read、完整计数/率、funnel-period 相容规则、outer 缺侧、allocation 精确公式/侧项/Top-K/Other/scope |
| F08 | inception、NotStarted/Unknown、legal/illegal/terminal/pre-inception trigger、区间/全部迁移/违规/主体分类与 end 左极限 |
| F09 | distribution 目标坐标/时点属性，transitions 完整 pair，dwell 完成片段与充分统计状态，各视图的 Subject/K 限制 |
| F10 | Anchor Event/Journey 起点、精确窗口、same-instant/DST、Metric 路径和重叠贡献；fixed 输入及禁止 mixed |
| F11 | retention 原 Ω/status-view/选人关系、实例→Subject 新 Ω、any/every、空 Ω 和不能上卷的 bounds |
| F12 | 每方法签名/规则/状态/实现版本、RequiredParts、检查时点、conditional K、资格键、资源预算、错误和披露 owner |
| F13 | 本地选人/Anchor 后观察的合法 placement；source 前缀的候选域/时间界、Metric 组件与历史属性交换 schema、本地限制/归约、scope/空组/资源及 mixed 拒绝 |
| F14 | 逐 producer 的 Evidence/Finding extractor/policy/body 及版本；资格/排序/cap/counts、身份/输入绑定、原子发布、公开读取、冷恢复与 exact-hit 校验 |

扫描范围包括 registry/module/worker/codec/runtime/Help/CLI/site 的真实消费链，记录基线、
AST/import 及 SQL 提交入口快照；静态数目不是动态 reachability。资格 ledger 至少按
§6 的每格登记 mandatory positive、negative、planned/blocked/unverified，不能只把旧
分派改成阻断后宣布迁移。未闭合的具体决定在编码前由 owning spec 接受，不放占位类型。

**出口：**F01–F14 无未决必需项，M01–M16 有符号/消费者/新 owner，V01–V18 有公共路线、
独立 oracle、真实 source/fixed 与恢复责任。R7.1 只完成文档/静态冻结，不授予 Runtime 资格。

Completed on 2026-10-01 at clean-entry `panda`,
`10724b1d5c54019e175a3a1c3e12a19ec9c4a118`: all fourteen decisions are closed in
the five sole owning specs. The migration ledger maps M01-M16 and V01-V18 to
actual baseline symbols/test definitions and future package owners. The snapshot
records deterministic AST/text/import evidence and mandatory source/fixed/cold
targets, all planned. Static presence, source admission blockage, unverified
dynamic unreachability and future deletion are distinct. See the
[R7.1 acceptance record](2026-09-26-marivo-full-refactor-acceptance.md#r71-documentation-and-static-freeze-completed-acceptance-2026-10-01)
for validation and excluded execution gates. R7.2 preparation, R7.3 Journey and R7.4 funnel and R7.5 canonical History implementations are recorded below; R7.6-R7.9 remain pending.

### R7.2 — Ibis occurrence 准备、覆盖与业务顺序消费

**2026-10-01 precision amendment:** native source/Ibis/driver time precision loss,
including ns→us, is accepted and disclosed. This overrides prior lossless-source
requirements without changing historical R7.1 snapshot or requirement IDs.
Checks/consumption use captured units; ties retain the closed business-order rules.
Receipt/Evidence/fixed/cold qualification retain declared, actual and captured
units plus native truncation/rounding or a possibly-lossy statement. No extra
lossless ticks extractor is required. Future public `.show()` must disclose it.

**Implementation record:** private captures, occurrence.prepare@v1, source-prefix
F13 and local count/int64/float64 sum/mean consumers are implemented. The
[R7.2 evidence index](2026-10-01-marivo-r72-evidence-index.md) owns executed
qualification and limits. Matching/replay/public domain integration and remaining
relative Metric expression/type cells stay with their connecting phases.

迁入 exact Event/StateModel/order 依赖、完整键/participant/time/version 准备和检查。
Assess source-native lowering first and record full-semantic feasibility,
transferred row/byte bounds and the precise local remainder for each physical
implementation. Keep backend-specific operators inside the single method
registry; ClickHouse candidates require supported Ibis bindings and R9 real
backend qualification. Do not treat R7.1 compile-only evidence as execution.
以既有 source/preparation/local-stage 协议消费，不新增 Event packet transport。
实现 F13 的 planner/lowering 依赖收集、Ibis 候选贡献准备与注册本地 observation consumer，
使后续观察的全部源依赖在本地阶段前完成。验证交换的完整键、组件/时间/历史属性、
空输入 schema、scope 和资源界；不以扩展 source-local allowlist 代替该执行形态。
同刻 integer sequence/enum/precedence 和拒绝/封闭不变性规则实际执行；覆盖绑定实际输入。
完成重复 trigger sharing、批次边界、空 schema、资源/取消与实际 Ibis→driver 提交审计。

**出口：**V01–V03/V13/V15 的准备与顺序格通过；错 participant、重复完整键、未知 enum、
无业务顺序以及“终态相同、违规身份不同”的反例按精确阶段失败；计划零业务 I/O。
F13 的准备计划/交换正例与无界准备、隐式回传、source-after-local 的拒绝通过；
实际 dropout 生产者到新观察的公共端到端出口归 R7.3。
不以准备成功声称 matching/replay 或新后端已通过。

### R7.3 — Journey matching、耗时、真值与主体映射

**Implementation evidence:** the [R7.3 evidence index](2026-10-01-marivo-r73-evidence-index.md)
records the public Journey/Duration/Completed graph path, three assignment policies,
retained publication and cold continuation, and A09 prepared Metric observation.
It owns the exact validation results, repaired failures and excluded qualification.
This implementation does not grant R7.4-R7.9, remote or full same-wheel acceptance.

实现三种 matching、canonical assignment/reach、JourneyResult、EventDurationResult、
CompletedJourneys、dropout read 与 subjects。接通普通 where/members、必要 Duration
row mean 和完整机会域的现有 cohort 消费；未接受 every_start dropout producer 不放行。
本包即完成状态/parts 的 publication、artifact_python 执行与 fresh-process 恢复。
接通 dropout/Duration 选人后的 Subject image 与 R7.2 本地 observation consumer，
在同一 Logical DAG/Run 中消费已准备贡献，不物化成员后回源或重新 matching。

**出口：**V04–V06/V13–V15 相关格通过，140/3 秒与 60 秒统计单位反例成立；source 新
求值变化、fixed assignment 保持；断源 reducer/read/selection 不 rematch。
A09 的 dropout→members→新 Metric 观察包含非空和空选择、完整组件/空组/scope 的
公共正例；执行轨迹证明所有 source 读取先于本地选人，显式 fixed+source 在读前拒绝。

### R7.4 — funnel、领域 compare/attribute 与单量 read

**Implementation evidence:** the [R7.4 evidence index](2026-10-02-marivo-r74-evidence-index.md)
and [qualification record](2026-10-02-marivo-r74-qualification.json) bind public
source/fixed funnel, exact owned reads, historical entry axes, ratio-mix allocation,
nonempty Findings and private Delta/Attribution retirement. Native/source matching
is not rerun: source preparation precedes the explicit `ibis_python` consumer;
fixed/cold execution uses `artifact_python`. All original P07/P08/P09 IDs remain:
18 K22/T01/T07 S/F/C cells are exercised here; the other 792 are unverified.
R7.4 is complete within this bounded local implementation scope. The passing
validation receipts and completion status are owned by the evidence index.
This bounded implementation grants no R7.5-R7.9, remote, same-wheel or full R7 acceptance.

实现 first_per_subject funnel、entry-time axes、封闭 funnel-period 比较和
funnel_ratio_mix，复用既有 AttributionResult/Relation/排名/table/transport。
逻辑补轴为同 assignment 显式依赖，fixed 只消费 retained axes。按 F14 接通非空
比较/贡献 Finding 的 extractor、闭合 body 与公共读取，扩展统一 graph 发布/恢复的
空集限定；复用 Store 事务和集合校验 owner，核验 count/digest/extractor 版本及绑定。
完成非空读取/恢复与原子失败后，迁出私有 funnel Delta/Attribution 的旧家族注册、
分派、extractor consumers 及 codec，不添加转发 alias。

**出口：**V07–V08 及 A09 的 funnel 部分通过；分组/未分组组件、每 resolution 的精确
oracle、零/缺侧/Unknown、共同 Top-K/Other、侧项和筛选 scope 均闭合，冷恢复不重新匹配。
V14/V15 的非空 Findings 发布、公开分页/单条读取、冷恢复/exact hit、篡改与失败注入
通过；compare 和 loss/denominator_mix contribution 均有非空 oracle，不以空集合过关。

### R7.5 — canonical Lifecycle replay 与主体分类

使用 R7.2 输入实现 from_inception 扫描；保留完整 legal transitions、violations、
canonical intervals 和每主体 coverage/known-prefix。删除 replay SQL/数组宏及相关
完整性文本调用，以注册本地检查消费同一完整输入。未保证全部相关轨迹不变的同刻输入拒绝。

**出口：**V09/V13–V15 相关格通过；跨输出窗口 inception、无区间主体、自循环/零时长、
terminal/illegal/pre-inception、不同违规身份及 end 排他反例成立；History 单独可断源恢复。

**实施记录（2026-10-02）：**公共 paired HistoryResult、完整成员与 occurrence 的双依赖、
canonical_history/HistoryPart@v1、注册本地扫描、断源校验和旧 replay 专属链退出已实现。
冻结 P10 的 270 格全部执行；fixed/cold 只读取并验证已发布 History，不构造新 replay。
相关 V09/V13–V15、披露和迁移证据见
[R7.5 evidence index](2026-10-02-marivo-r75-evidence-index.md) 与
[逐格 qualification record](2026-10-02-marivo-r75-qualification.json)。
V10/V11 及完整 R7/same-wheel 原始 requirement ID 保留并明确后置；本轮不开放 R7.6 视图。

### R7.6 — History 视图、Duration 统计与跨家族续算

接通 in_state、distribution、transitions、violations、intervals、dwell 的具体结果和
owned Relation。复用 where/members/SubjectBinding；满足 C12/C13 所需的 Duration
方法精度与空状态，不借助旧 Dataset reducer 或 Artifact DuckDB。
状态/违规/区间选人后的新 Metric 观察复用 F13 的准备与本地消费，保持实际主体像与 scope。

**出口：**V10–V11 及 A10 通过；每视图有独立原始事件/区间 oracle；仅区间无法恢复
迁移、摘要无法恢复主体/原分布的负例拒绝；NotStarted/Unknown 和 completed fragment
分类正确；所有已披露 fixed K 在源/模型不可用的新进程中实际执行。

### R7.7 — Anchor 域、时间窗口与相对 Metric 观察

实现 session.anchors、elapsed/calendar_days、Event/Journey 起点和 relative observe。
复用 R5 Metric/RuntimeMetricExpr 各组件方法、路径/版本/时间 owner，保留 Anchor 实例、
窗口、贡献重叠与状态；新增普适规则只进入当前 core/method owner。
Journey 起点消费 F13 的有界候选贡献，在本地按实际 Anchor 窗口限制；不得把本地产生的
Anchor 上传到源，或重新 matching 取得一个可下推的替代域。

**出口：**V12/V13–V15 的 Anchor 格通过；DST elapsed/calendar deadline、同刻排除/顺序、
多 Anchor 重数、重叠贡献、multi-root 和不可原状态 rollup 的反例成立；fixed Journey
起点不 rematch，纯固定已保留观察续算不回源。

### R7.8 — retention 固定 Ω、未知界与显式主体量化

实现 instance retention、by_subject 的 any/every、完整 status partition、bounds、
可决定真值选择与成员像；在统一状态/receipt 中保存原 Ω、窗口/覆盖、规则及新主体 Ω。
不增加 generic bounds arithmetic、Unknown drop 或另一条选人路径。

**出口：**V12/V14–V15 的 retention 格和 A13 通过；25/5/70、空 Ω、一真一未知/一假一
未知、观察真且不完整随访、无返回已完整/未知、共享返回、无 Anchor 主体反例闭合。
断源 by_subject/status selection 固定续算保持原 Ω 与全部 K。

### R7.9 — 旧消费者退出、工程/同一 wheel 收口与交接

关闭 M01–M16，迁移/删除旧测试和 worker 并保留独立反例。跑 V01–V18、A09/A10/A13、
受影响 A02/A07/A08 及 J1–J4 公共回归；完成同一非 editable wheel 的 source/fixed/cold
旅程。原始日志与最终/失败候选分开，更新 tracked ledger/evidence index 和验收主记录。

**出口：**§7 全部满足；R7 只剩新统一执行/恢复路径，R8/R9 共享残留有真实 owner，
不留 R7 专属旧 SQL 交给 R9 删除。未完成必需格不能以阶段编号或大测试总数宣称通过。

## 5. 迁移、删除与共享责任

以下为起草时实际路径，R7.1 逐符号反查后补完整 caller/import/worker/codec 和去向。
新模块名按既有责任落实，不创建空 facade 或转发模块维持旧 import。

| ID | 当前消费者/实现 | 目标处置与删除门禁 |
| --- | --- | --- |
| M01 | session/_lazy_sources.py、session/core.py 的 Event/Lifecycle source；subject/PopulationInput | 接入新 AnalysisDomain 与 graph；R7 旧 membership/source 分支退出，R8 实际依赖单列，不整目录清空 |
| M02 | domains/event.py、domains/contracts.py 的旧 Journey payload/semantics/register | 分解为闭合领域构造与统一方法状态；旧 Logical/MaterializedEventDataset、family source producer 退出 |
| M03 | compiler/event.py、event_sources.py、event_time.py、event_axes.py | Ibis occurrence/axes 准备与注册 matcher；删除原场景编译/身份排序准入及 backend 分派 |
| M04 | domains/event_reducers.py、compiler/event_reducers.py、event_continuation.py | funnel/duration/read/映射接入统一规则，保留必要算法；旧 select_subjects 与旧 reducer 分派退出 |
| M05 | domains/funnel_delta.py、funnel_attribution.py、funnel_registry.py；event_comparison/attribution 与 *_values | 领域 FunnelComparisonResult 与现有公共 AttributionResult；保留已核验算术，删除 private Delta 家族/旧 registry/恢复链 |
| M06 | materialization/event_codec.py、event_publication.py、event_reducer_*、event_comparison_* | method-specific schema/校验并入统一 graph receipt/publication；旧 descriptor 专属 codec 不双读 |
| M07 | domains/lifecycle.py、lifecycle_reducers.py 与旧 LifecycleDataset types | History 与闭合领域视图/主体分类；旧 payload、family register、select_subjects、泛化 where 接口退出 |
| M08 | compiler/lifecycle.py、lifecycle_array.py、lifecycle_reducers.py | Ibis 准备→注册 replay/reducer；删除 recursive/array SQL、ID 排序业务依据和文本 confluence 检查 |
| M09 | materialization/lifecycle_codec.py、lifecycle_publication.py、lifecycle_reducer_* | canonical state/parts 与统一发布/恢复校验；native_summary/inspect_history 不再 statement 查询 |
| M10 | materialization/{postgres,trino,clickhouse}_event_sql.py、event_bundle.py、lifecycle_bundle.py、lifecycle_integrity.py | 删除 Event packet、私有 Ibis visitor/补丁、手写 replay/完整性 SQL；不封装进新 helper/adapter |
| M11 | materialization/{postgres,trino,clickhouse}_execution.py、source_stage.py 的 Event/History 专属调用 | 反查 AN07/AN23 部分责任，删除 R7 source 前缀/收集/summary/bundle；共享 adapter 生命周期保留真实 owner |
| M12 | operators/registry.py、compiler/{source_admission,placement,lowering,graph_plan,graph_lowering}.py、materialization/{dataset_execution,graph_observation,graph_local_execution}.py | R7 method/physical qualification 归单一 methods；落实 F13 的 source 前缀与本地观察，显式依赖/交换/资源/拒绝同迁；旧 Event/Lifecycle/funnel 分派和 migration markers 退出，R8 阻断不解除 |
| M13 | core、graph snapshot/runtime/publication、Session artifact 恢复 | 新领域节点/部件进入当前协议；旧状态读前拒绝，无 alias、迁移、双读或 origin replay |
| M14 | evidence/_finding_registry.py、materialization/{finding_values,input_bindings_codec,graph_publication,graph_store,store}.py 与 Session/Event/Lifecycle Evidence/Finding 读取 | F14 在现有 Store owner 落地，替换 graph 的无条件空 Findings 校验；原子发布非空集合并校验 digest/count/版本/绑定，公开读取与冷恢复/exact hit 同迁；旧 Event body variants 随消费者退出，领域违规不自动变 Finding，R8 共用绑定保留 |
| M15 | __all__、native Help/_capabilities/introspection、CLI、API docs、site latest EN/ZH、packaged skills | 导出快照、独立 reachability/drift/budget、动态 K/repair、精确 typing 与示例同迁；skill 按批准要求处理 |
| M16 | tests/test_lazy_event_*、test_lazy_lifecycle_*、funnel comparison/attribute、remote tests/fixtures/workers | 逐测试节点记录 replacement/retirement；保留算术/业务 oracle，删除旧入口/codec/身份排序要求，远端资格归 R9 而非伪造本机通过 |

[SQL ledger](2026-09-26-marivo-full-refactor-r0-sql-ledger.md) 的 AN02–AN10、AN23 Event
部分在 R7 关闭；R9 复核全后端真实资格和总体 SQL 审计，不承接尚未迁完的 R7 算法。
AN11/AN12 已由 R6.7 删除，不重建来支持 funnel。R8 的候选/forecast/association 与
真实共用算术、predicate、key/reconciliation helper 按当前消费者保留，不因命名相近删除。

ledger 对每项同时记录“新 producer/consumer 可执行”“旧注册/导入/Runtime/codec 不可达”
与“物理文件删除/共享保留”。只去掉 __all__、全拒绝或 AST 数目下降不能完成迁移。
历史文档可保留历史状态；当前 Help、错误、site 和示例不能指向退休入口。

## 6. 验收矩阵、资格格与独立 oracle

### 6.1 V01–V18

| ID | 必需公共正例/独立预期 | 反例与拒绝/保持要求 |
| --- | --- | --- |
| V01 身份/构造 | string/int64 及复合 Subject/occurrence K、重复 Event refs、explicit members、source/fixed 类型；计划零业务 I/O | 缺/重复/空键、foreign Session、错 role/model/steps、to-many、version overlap、旧 PopulationInput、mixed 早拒绝 |
| V02 顺序 | integer sequence、ordered enum、precedence；相关轨迹不变的封闭正例 | 非法/重复 sequence、未知 enum、同刻多允许顺序改变 assignment/interval/violation；终态相同而违规 ID 不同仍拒绝 |
| V03 覆盖 | exact bounded/source-origin、observed/declared/mixed；窄 attempt interval 已完整 | 错 Event/source/version、矛盾 claim、max/count/empty 伪证明、bounded 冒充 origin；不足与损坏分开 |
| V04 matching | one/two/three-step、重复 Event、first、every shared/exclusive、canonical dense assignment；小型独立逐 occurrence oracle | occurrence 填两步、越过缺步骤、final reservation 侵入中间复用、行重排改变结果、无开始补失败 |
| V05 duration | exact 非相邻步骤对、complete/incomplete/censored/not-entered/entry-unknown、140/3 秒 oracle | foreign 同名 step、observed 冒充完成、最后事件时间作 followup、float tick 丢失、默认主体去重、非法/负 duration |
| V06 dropout/映射/cohort | first dropout truth、同 Run where→members→已准备贡献上的新观察；非空/空选择、scope/组件/空组；Journey/Interval/Anchor Subject image 与完整机会消费 | Unknown 静默排除、缺事实反推机会域、错 binding/subdomain、摘要重建成员、every_start 默认主体漏斗/dropout；物化成员后混入源观察 |
| V07 funnel/compare/read | dense steps、无轴空输入、entry-time axes、分组 exact 组件、完整 outer、三率/零分母、同一 assignment 单量 read | checkpoint/current axes 代入口 axes；未知流失、period/followup 不同、缺侧无依据补零、独立成员捕获、every_start funnel |
| V08 funnel allocation | loss/denominator_mix 精确分数 oracle、共同 Top-K、typed Other、joint/hierarchy、每层侧项/target 核对、筛选 scope | 两期独立 Top-K、公式换成分组比率差、零分母、缺组件/轴回源、零 residual 伪完整、初始/foreign step |
| V09 replay | 独立小型状态机 oracle、窗口前 inception、自循环/zero-duration、illegal/terminal、无 trigger NotStarted、完整主体分类 | 默认 initial、缺 inception 已完整仍继续、same-state trace 丢失、违规定义/occurrence 改写、end 事件进入历史 |
| V10 History 视图/选人 | end 左极限、known prefix、零状态/pair、checkpoint axes、无区间主体、violation/interval where→members | 只有区间重建 transition、未知分母当全体、distribution 用入口属性、缺 coverage 主体漏行、ModelState 汇总反推 Subject |
| V11 dwell/数值 | independent clipped interval oracle、left-clipped complete、right/coverage-censor 排除、精确 mean/median/p90、空状态 | 完成片段当完整生命周期、组均值/p90 平均、非有限/溢出、tick 舍入丢失、不充分 scalar state 获 rollup |
| V12 Anchor/retention | Event/Journey 起点、DST 168h/7days、shared overlap、multi-root observe；25/5/70、空 Ω、any/every 真值表 | Anchor 自身算返回、同刻无业务先后、隐式 exclusive、无 Anchor 补失败、删未知改分母、默认主体去重、bounds rollup |
| V13 Runtime/identity | source 新 Run 与源变化、explicit sharing、同一 assignment/history 多视图共用、固定精确 hit；F13 全部源依赖先准备、实际本地选择/Anchor 限制后观察的执行轨迹 | Equal definition 独立捕获自动共享、隐藏重选人/match/replay、上传本地结果或 source-after-local、旧 source cache、失败后 fallback/重试、检查/消费源不一致 |
| V14 exchange/recovery/L6 | 全部状态/定义/ordered input/parts/K 比较，produce→continue→recover 独立进程，断源实际执行每项可用 K；非空比较/贡献 Findings 的 evidence_digest、分页/单条读取、count/set digest/extractor 版本与绑定保持 | 每 required part 或 Finding 缺失/损坏/交换/错 binding/version；Evidence count/digest/版本篡改、错误空集、foreign finding ID；旧 snapshot，错误 schema/Cell/key/state；在读取/恢复/K/cache 前拒绝，无新增 Run |
| V15 资源/原子性/SQL | real BatchStream 跨批、空 schema、取消/超时/提前 close；Ibis 签发 SQL 与 driver 原样提交；extractor/事务失败注入和 Artifact/Evidence/Findings/terminal 原子性 | partial artifact/Findings、泄漏 cursor/reader/staging、无界候选准备/collect、预算截断称完整、跳过坏 Finding；手写 packet/macro/visitor/AST 补丁；失败保留旧 Artifact |
| V16 删除与共享 owner | M01–M16 的导入/registry/runtime/codec/worker 反查；AN02–AN10/AN23 Event 链关闭 | 旧 import/Help alias、source executor/固定 DuckDB 路线、专属 codec 双读、被改名隐藏 SQL；误删 R8 共享 consumers |
| V17 public disclosure/typing | 有界 repr/show/K、Help 独立可达/预算、导出快照、positive/negative typing、CLI、API 和英中示例 | 可执行属性与 contract 分裂、Any/动态列、terminal table 回灌、错误修复指退休入口、非 Defined 选人暗示 |
| V18 同一 wheel/旅程 | A09/A10/A13 及受影响公共回归；安装 origin/hash/依赖、断源与 poisoned PYTHONPATH；F13 同 Run 观察与 F14 非空 Findings 读取/冷恢复均在该 wheel 验收 | 可编辑安装/偷 import checkout、不同 wheel 拼通过、恢复偷 load Semantic/连源/DuckDB、只核数字而不核 parts/K/Findings |

L1 在共同全定义、同机会域上验证连续选择与合法合取，同时保留非 Defined 的拒绝差异；
L6 检查恢复前后实际 K/语义与部件，不只比较 repr。相关合法数值原状态归约按 L8/L9
验证完整状态及含空组目标；领域 replay/重叠 Anchor 不自动继承 bag reduction 律。
有限 oracle/测试不宣称通用 matching/replay 或无序事件的形式化证明。
Findings 使用独立预期行集验证资格、确定性排序、cap 与 eligible/emitted/truncated counts，
覆盖无合格行的合法空集、超过 cap 的非空集、loss/denominator_mix 侧项及禁止原始身份
泄露；不能从被测 registry/extractor 反射生成预期或只比较 finding_count。

### 6.2 方法资格与最小必需正例

资格键必须含 method/version、ordered domain/value types、Subject/occurrence 键形状、
source form、backend/table kind、exact time shape、route、implementation/version 和资源。
每下列必需 source 格分别跑 native table、local Parquet；可续算方法另跑实际
artifact_python、发布与新进程恢复，不能继承前序 method 名字的通过。

| 方法簇 | R7 必需正例与状态 | 资格边界 |
| --- | --- | --- |
| occurrence/order/matching | 单/复合 string/int64 身份、无/有业务同刻、重复 Event、三 matching、单/多步、零/多 attempt | DATE/未解析 naive occurrence 不能自动当 instant；不合格类型在读前拒绝 |
| Journey duration/truth/image | 精确步骤对、全部状态、Duration 比较与必要 mean、Subject image/完整机会 | R7.1 固定 Duration 单位/结果/舍入；每种实际单位与 fixed 路线独立验证 |
| 本地选人后观察 | dropout/History 选人→members→Metric；F13 有界源准备、本地精确主体限制、非空/空域与组件/历史属性 | 必需 source 正例使用同一 Logical DAG/Run；显式 fixed+source 仍拒绝，fixed 仅使用足够的既有部件 |
| funnel/compare/allocation | exact int64 counts、float64 finish/Undefined、完整和缺侧组、entry-time axes、joint/hierarchy/Top-K | 组件精确到 finish；不准入不完整 followup、every_start 主体漏斗或未保留轴 |
| Evidence/Findings | compare/attribute 非空 algebraic Findings、公开分页/单条读取、集合/版本校验、原子发布和冷恢复；zero policy 与无合格行的空集 | 逐 producer/extractor/policy 资格，不继承旧 codec；cap/排序/截断独立验证，不将 violations 变为 Findings |
| History/reducers | source-origin/unknown、完整每主体分类、多 trigger/状态、self/zero-duration、checkpoint、Duration mean/median/p90 | 绑定具体时间和模型形状；不授予一般统计、任意 replay/seed 或跨段合并资格 |
| relative observe | count/sum 和 multi-root Ratio/RuntimeMetricExpr，R5 已接受数值/路径/空政策在相对窗口重新验收 | 所需其他已接受 Metric/时间方法逐格列明；没有该格的正例保持 blocked/unverified，不能泛化为全 Metric 支持 |
| retention/by_subject | exact status sets/int64 counts、finite bounds/empty Undefined、elapsed/calendar、共享重叠、两种量词 | 只开放已接受固定总体和 K；不提供 scalar bounds 的归约/算术 |

时间必需格包括 UTC aware instant、report-local aware/DST；native microsecond 和 Parquet
s/ms/us/ns 的实际表示/往返/非整 tick 边界在 R7.1 落到具体方法。沿用前序 precision/
timezone owner，单位或精度不支持不能截断到 microsecond 后称通过。日历窗口 additionally
验证春/秋 DST、deadline 排他、ambiguous/nonexistent local time 的已接受政策或拒绝。
exact 历史属性至少覆盖 snapshot/validity 的入口/checkpoint 绑定；不从“R5 支持”推断
Event/Lifecycle temporal consumer 已通过。

R7.1 以 requirement ID 固定完整必需格，不能到收口时删除失败格或把必需项改成 unsupported。
通过、失败、阻塞、未验证、显式非适用和 skip 分开；远端旧测试或编译通过不能给 R9 资格。
需要 source ibis 和 ibis_python 双实现的仅为本轮实际准入路线，同输入向量验证语义/状态；
只准入 ibis_python 的 matcher/replayer 不为证明“全下推”重建 SQL。

### 6.3 公共用户旅程

- **A09：**正式声明/Session members→matching→funnel→期间 compare/attribute/read；
  exact pair duration→统计/筛选→Subject image；dropout→members→新 Metric 观察。
  包含 every_start 重数、Unknown 完整选人拒绝及 Logical 同 assignment 补轴。
  新观察验证 F13 的同 Run 源准备/本地选择及空选择；compare/attribute 分别产生非空
  Findings，公开读取 evidence_digest、findings() 分页与 finding()，并验证冷恢复。
- **A10：**正式 StateModel/business_order→from_inception→history→时点状态选人；
  transitions/violations/intervals/distribution/dwell；same terminal/different violation 反例，
  保留前窗 inception、end 左极限、无区间 Unknown/NotStarted 的完整主体；选人后沿
  F13 在同 Run 对已准备贡献观察新 Metric，验证主体像和空组/scope。
- **A13：**Event 和 Journey 两种 Anchor 起点→相对 Metric 观察/retention；DST、多个 Anchor、
  shared overlap、25/5/70、any/every 的不同主体结论，已知真值成员像和断源续算。

各旅程从 `import marivo.datasource as md`、`import marivo.semantic as ms`、
`import marivo.analysis as mv` 的公开入口开始；新公共脚本/fixtures 保持英文。
producer、continue、recover 使用独立进程；改变源事实证明 source 新实现，再移除数据库、
models 和 Parquet，禁 current Semantic/source/DuckDB 连接验证已承诺 fixed K。
cold members/read 是固定动作；“选人再新来源观察”在正常有源进程按 F13 构造同一
Logical DAG 验收，不复用已物化成员混入 live source，也不把它伪称断源能力。
J1–J4 与受影响 A02/A07/A08 覆盖共同图、选人、归因、机会与恢复回归；脚本不替真实 Agent。

## 7. 工程门禁、证据与完成判定

日常使用最窄的 `make test TESTS='...'`、`make runtime-test TESTS='...'`、
`make typecheck TYPECHECK_TARGETS='...'` 和 `make lint-agent LINT_TARGETS='...'`。
新增/修改 Python tests、fixtures、workers 前使用仓库 marivo-test-fixtures skill；
R7.1 将本文测试责任映射到实际节点，不把尚不存在的测试文件写成已执行命令。

各公共/共享包收口运行 `make check-agent`、必要定向 Runtime、
`npm --prefix site run build` 与 `git diff --check`。default skip 不计 Runtime 通过；
新状态/codec 必含真实新进程和故障注入。静态 import/registry/SQL 扫描与实际 driver
提交审计互补；恢复测试同时禁止 Semantic 和 source/DuckDB 连接。

R7.9 通过 `make pypi-build pypi-check` 构建并校验同一非 editable 候选 wheel/sdist；
记录 source/test inventory、依赖和 hash。隔离环境每个进程（含 worker/subprocess）确认
site-packages origin 与候选 hash，poisoned source PYTHONPATH 必须被检测拒绝。
全 R7 状态/反例/用户旅程和受影响共同回归用同一 wheel；非空 Findings 的 public reads、
冷恢复/exact hit 必须核对独立预期 body、身份/输入绑定、集合 digest/count 与版本。
修复后重构候选并重跑必需最终门禁，不拼接不同源码或 wheel 的通过。
该 gate 是本地安装资格，不是 R9/R10/release。

R7 ledger/evidence index 记录：F/M/V/能力及方法 ID、owning spec 摘要、SHA/dirty diff、
环境/依赖、exact source/time/type/route、实际命令/退出码、独立预期、driver/receipt/parts/K、
资源关闭、失败复现、skip/blocker/恢复条件与消费者去向。大型原始附件可在 ignored 目录，
tracked index 必须保存 hashes、结果摘要和可复现入口；不能仅指向不可取得的本地日志。
失败/中止/被取代候选独立保留，不借它们授予最终候选资格。

R7 完成须同时满足：

1. F01–F14 已由唯一 owner 接受；C11–C13/C18 的必需正例、反例和精确资格全部闭合，
   不以全拒绝、未来扩展或阶段外移绕过必需业务能力。
2. matching/replay/Anchor/retention 从公共 Logical/Materialized 入口经过既有图、registry、
   Runtime/Store；reducers 消费同一 canonical retained result，无重匹配/重放/汇总补主体。
   本地选人/Anchor 后观察的源准备与本地消费均有注册 owner、公开正例和执行轨迹，
   不上传本地结果、不隐藏 source-after-local，不解除显式 source/fixed mixed 禁令。
3. 业务顺序、覆盖、Journey/Subject/Interval/Anchor 单位、时间左极限/相对窗口、完整 Ω
   与 Unknown、组件/数值政策分别有独立反例；同终态不能代替轨迹等价。
4. V01–V18、A09/A10/A13 与受影响基础回归、同一 wheel 和断源 K 实际通过；非空
   Findings 的原子发布、公开读取、冷恢复/exact hit 和完整性反例通过；失败、skip、
   静态/Runtime/package 证据各自有明确处置，未验证不算通过。
5. M01–M16 和 AN02–AN10/AN23 Event 消费链退出；仅剩真实 R8/R9 共享 owner，无旧 SQL、
   家族 executor、source cache、fixed DuckDB、alias、迁移或 dual read。
6. disclosure、strict typing、API、Help/动态 K/repair、CLI、最新英中示例一致；必要 skill
   同步已有明确批准与完成记录，不能以本计划代替该批准。
7. R8 得到领域数值视图/Subject/机会消费与 Duration 精确资格边界，R9 得到逐方法后端/
   物理形态/时间/资源矩阵和实际 SQL 证据，R10 得到可复现安装旅程/失败记录。

本实施文档落盘只完成 R7 规划，不创建任何产品资格或阶段通过记录。
