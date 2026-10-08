# Marivo 全量分析代数与 Analysis DSL：R8 实施文档

Historical execution records, qualification inventories and one-time validation
scripts were removed during the 2026-10-06 cleanup. Committed records remain in
Git history; local-only execution files were discarded. Recorded phase results
below describe their original scope.

Date: 2026-10-03

Status: R8.1 documentation/static freeze complete. R8.2 implementation and
qualification are in progress; see the R8.2 evidence index (historical record in Git history).
The final R8.2 ledger retains 15,564 mandatory IDs: 13,212 passed, 2,280 blocked
and 72 unverified. R8.2 is not complete.
R8.3 implementation and bounded verification are in progress; see the
R8.3 evidence index (historical record in Git history).
R8.4 product connection and bounded verification are recorded in the
R8.4 evidence index (historical record in Git history); its mandatory matrix remains open.
R8.5 public cutover and retirement are complete: 171/171 obligations passed and
M01-M18 gates closed; see the R8.5 evidence index (historical record in Git history).
R8.6 local implementation and same-candidate verification are recorded in the
R8.6 evidence index (historical record in Git history): 287/492 original
obligations passed and 205 remain unverified. R8.6 and full R8 are not complete.
The accepted 2026-10-02 C14 scope revision is the planning input;
static inventory, Runtime, installed-wheel, backend and real-Agent evidence
remain separate.

## 1. 目标、前置交接与文档权威

执行[总实施计划 R8](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md#r8--统计与数值扩展接入)，
完成 C14.a1 偏离评分、C14.a2 连续区间、C14.b 多量相关与 lag、C14.c 预测及固定结果视图。
全部进入现有 typed graph、唯一 method registry、Runtime、受控交换与 Store 7，随后退役
旧 discover、Candidate 和相关/预测的专属 Dataset 执行链。

用户确认 R0/R1/R2/R3/R4/R5/R6/R7 任务完成，以此启动 R8 规划。起草分支 `panda`，
读取基线 HEAD 为 `d853307742f1825e7ed8c468baaefc4cba8b4245`。工作树已有前序产品、
测试和文档改动，部分历史资格附件已取消 Git 跟踪，并有 R7.9 新文件。本次仅新增本文，
不整理、提交或覆盖这些状态；实施 R8.1 时重新绑定实际 SHA、dirty diff、新增文件和输入摘要。

前序交接读取验收主记录 (historical record in Git history)、
R6 migration ledger (historical record in Git history)、
R7 migration ledger (historical record in Git history)及
R7.9 evidence index (historical record in Git history)。起草时
R7.9 audit (historical record in Git history)仍记录原始部分资格目标未完成，与本次
用户的阶段完成交接分别保留；本文不改写历史验收或替前序补授资格。R8 的主依赖为 R5/R6，
可以先编写和冻结自身契约；实际消费某项 R7 部件时按其已记录契约检查，不依赖未取得的资格。

2026-10-02 的[接口设计 §8.4](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md#84-数值评分时间区间与统计模型)
已替换“旧 discover 五方法全部保留”的要求。新目标不与旧滑窗、绝对分数包装或排轴启发式
承诺等价。历史成功结果保留其原身份，不能作为新评分、区间或多量相关的验收结论。
本文细化工作包与出口，不以落盘替代 R8.1 的具体契约冻结或后续执行。

### 1.1 范围与阶段边界

| 能力 | R8 必需交付 | 边界 |
| --- | --- | --- |
| C14.a1 | `NumericRelation.deviation`；zscore/MAD；同原域四个固定视图、显式分类分区、拟合依据与状态 | method 必填；无自动阈值、自动分区、统计显著性、概率或贡献解释 |
| C14.a2 | `NumericRelation.runs`；完整格上最大连续段、条件状态、边界、格数、实际 Duration 与真实 Subject 映射 | 与评分独立；无 rolling、峰值排名、最短长度或方向推断参数 |
| C14.b | 2–16 个互异单量输入、完整两两组合；Pearson/Spearman/Kendall tau-b；获准 lag；固定 coefficient/selected 视图 | 无隐式 inner join、分批独立求秩、p-value、因果或最优 lag 效果解释 |
| C14.c | naive/drift/seasonal_naive；`normal_residual@v1`；训练域、获准未来格与 prediction/lower/upper 视图 | 无插补、自动选模、bootstrap、实际覆盖率保证或区间加总 |
| C15/C16 增量 | 来源新求值、图内共享、固定精确命中、原子 Evidence/Findings、断源恢复、类型/Help/repair/文档 | 复用既有 owner，不新增统计 namespace、公共注册器或第二套 Runtime |
| 旧链退出 | discover 五包装、Candidate 专属链、旧 Association/Forecast Dataset 生产者和协议消费者 | 共享 helper 按符号及真实调用方退出；历史文档和无关“discovery”用语不按词清除 |

本地资格覆盖冻结的真实 DuckDB table/Parquet 来源、`ibis_python` 准备后计算及
`artifact_python` 固定计算，并按注册情况单列 Pearson/Spearman 的合格 Ibis 源端路线。
int64、float64、Decimal 的必需数值目标均须在 R8.1 展开；未完成的必需类型保留阻塞，
不能临时改为“不支持”以宣布 R8 完成。六后端及其全部物理形态由 R9 逐格资格验证。

Duration 是 runs 的输出及已接受的单位化筛选输入，不因本阶段增加 Duration 排名或通用
Duration 统计目录。已撤回的 statistical_weight authoring、本轮未接受的 rolling、
concentration、通用 search/planner、跨源 mixed、任意 callback、因果/生存推断不重新引入。
真实 Agent、最终发布与全后端接受分别由 R10/R9 负责。

### 1.2 唯一契约 owner

| Owner | 责任与本阶段增量 |
| --- | --- |
| [DSL 接口设计 §8.4](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md#84-数值评分时间区间与统计模型) | C14 业务含义、唯一入口、四类方法的目标视图及旧五法的主动退出 |
| [DSL 架构 §3.2.2](2026-09-24-marivo-analysis-dsl-architecture-design.md#322-r8-的评分与连续区间边界2026-10-02-修订) | Deviation/Runs 的图、parts、放置、共享与恢复边界 |
| [Python Analysis design](../../specs/analysis/python-analysis-design.md) | R8.1 归入具体 Logical/Materialized 签名、类型重载、字段所有权与公共族协议 |
| [Operators and frames](../../specs/analysis/operators-and-frames.md) | 完整规则、Cell/数值政策、配对和统计状态、RequiredParts、PartTransform、K 与修复义务 |
| [Session/Runtime](../../specs/analysis/session-state-and-runtime.md) | 输入与实现身份、source-prefix、交换、资源/截止时间、Store 7、Evidence/Findings、恢复及 exact hit |
| [Timezone/calendar](../../specs/analysis/timezone-and-calendar-design.md) | 完整格、认证日历、邻接、精度、时区、lag 坐标和未来延续；不由数值算法重定义时间 |
| [SQL ledger](2026-09-26-marivo-full-refactor-r0-sql-ledger.md)及验收主记录 | 当前消费者、内部 SQL 退出、具体资格格、实际证据、失败与 R9/R10 交接 |

[旧 typed operators 的 correlate/forecast 契约](2026-09-01-lazy-analysis-typed-operators-design.md)
及当前 `association_contracts`/`association_values`、`forecast_contracts`/`forecast_values`
提供保留的方法、配对/选择规则和预测公式。R8.1 将其适用内容归入当前 owning specs，
逐项处理新 Relation 输入与旧 Dataset/codec 的冲突；旧容器、family 分派和序列化不是保留要求。
方法语义只保留一个权威，Help 不另建公式清单，本文中的摘要不形成第二份公式 owner。
R8.1 冻结后的规范已归入 [Operators owner](../../specs/analysis/operators-and-frames.md#statistical-methods)；
旧文档顶部交接声明保留历史输入身份，不再拥有新 Relation 的方法规则。

## 2. 必须闭合的公开契约、状态与续算

### 2.1 目标入口与具体变体

以下为目标签名摘要，尚不是当前可执行 API。R8.1 必须同时冻结 Logical 和 Materialized
接收者/输入的精确重载；构造只产生新的 Logical，唯一 `.execute()` 发布固定结果。

```text
NumericRelation.deviation(*,
    method: Literal["zscore", "mad"],
    partition_by: tuple[CategoryRelation, ...] = ()
) -> LogicalDeviationResult
DeviationResult.observed/reference/deviation/score -> NumericRelation
DeviationResult.where(BoundPredicate) -> LogicalDeviationResult

NumericRelation.runs(*, where: BoundPredicate) -> LogicalTimeRunResult
TimeRunResult.start/end -> TimeRelation
TimeRunResult.count -> NumericRelation
TimeRunResult.duration -> DurationRelation
TimeRunResult.where(BoundPredicate) -> LogicalTimeRunResult

NumericRelation.correlate(*others: NumericRelation,
    method: Literal["pearson", "spearman", "kendall"] = "pearson",
    lag_range: range | None = None
) -> LogicalAssociationResult
AssociationResult.coefficient -> NumericRelation
AssociationResult.selected -> BooleanRelation
AssociationResult.where(BoundPredicate) -> LogicalAssociationResult

NumericRelation.forecast(*,
    horizon: ForecastHorizon,
    model: ForecastModel = naive(),
    interval_level: float = 0.95
) -> LogicalForecastResult
ForecastResult.prediction/lower/upper -> NumericRelation
ForecastResult.where(BoundPredicate) -> LogicalForecastResult
```

`NumericRelation`/`CategoryRelation`/`TimeRelation` 等是此处的契约族称谓，不能直接作为新增
顶层 export 或用一个可选字段 mega-class 实现。以当前具体变体冻结返回类型，尤其对齐
现有 Temporal/Duration 类型。Deviation 与 TimeRun 是必要的新结果族；Association 扩展
已有 Result，Forecast 迁入同一 Result 协议，不恢复 CandidateResult、字符串列 lookup
或任意字典视图。`ForecastHorizon`/`ForecastModel` 与四个工厂沿用其闭合参数值。

现有公共 `correlate(other, method="spearman")` 只承接双量 same-Entity no-lag 切片。
新签名默认 Pearson、支持多量和 lag；明确记录 breaking change，并在旧回归中显式传
`method="spearman"`。不保留双默认、旧 Dataset 重载或同义工厂。

### 2.2 偏离评分：固定拟合域、完整原身份与数值政策

方法为 `deviation.zscore@v1` 和 `deviation.mad@v1`。按接口 owner：zscore 使用有效值
均值及总体标准差；MAD 使用中位数、1.4826 倍中位绝对偏差，原 MAD 为零时使用围绕
同一中位数的平均绝对偏差，并保留实际尺度分支。两者分数均有符号。

- 输入观察值、Difference 或其他合格数值量均按同一方法定义评分。Time/Entity 只决定
  域和实际 K，不选择算法。method 必填，空 `partition_by` 在整个接收者域拟合；显式
  分类关系遵循已有同域对应、Null 类别及 owner 规则，不自动按其余坐标分区。
- 每分区的有效样本为 Defined 且有限的值，等行权。Null/Undefined/Unknown 不参与拟合，
  但完整原行、状态原因及各状态计数保留。非有限 Defined、重复身份或来源覆盖违约原子
  拒绝，不能作为“排除样本”。原域数量与有效 n 不互换。
- 空有效样本、单样本、零尺度分别有结构化原因；n<2 或最终尺度为零时，有效行 score
  为 Undefined。无可用评分不能披露成“没有异常”。R8.1 固定四个视图在无中心、有效
  中心但无尺度及原非定义 Cell 时的逐状态表，不能用物理 NULL 一并编码。
- observed 保留原值、身份、单位及其获准部件；reference/deviation 为原单位，score
  无量纲。后三者的统计定义不继承原 Metric 组件或原量 rollup/attribute 许可。
- 拟合 scope 在 deviation 节点固定。结果 where 同步选择四视图；视图 rank/limit 为
  既有 RankingResult，保留原 n/中心/尺度/分区与输入绑定，不新增其他评分视图。
  `where→deviation` 拟合新子域；`deviation→where/rank/limit` 不重拟合，优化不能跨越此边界。
- 多个视图或两个消费者共享同一个显式输入/拟合节点，完整准备只做一次；分别构造的
  同形节点不自动合并。选择 observed 后的 members 消费真实原主体映射，不把 score
  数值或排名行号当 Subject identity。

数值政策不是“调用 NumPy 后存 float64”。R8.1 按每个方法、类型和输出视图冻结如下事项：

| 类型/环节 | 必需冻结与 oracle |
| --- | --- |
| int64 | 大整数差值、求和/平方及偶数样本中位数的中间表示；不能先转 float 合并不同值；输出表示及范围检查 |
| float64 | 中心/尺度的稳定计算、总体方差、行序/批次误差界、近消去/次正规数；不以无依据 epsilon 将尺度夹成零 |
| Decimal | 输入 precision/scale、中心/差值/尺度/分数的输出精度、1.4826 常量表示、开方与除法舍入、上下文与溢出；无未经定义的 float 强转 |
| 全类型 | 运算和最终舍入位置、有限性、计数溢出、尺度分支、零值判定、误差/近似披露及类型混合的封闭准入矩阵 |

独立预期从原始整数/Decimal、均值/方差定义、排序取中位数及状态表求得；不得导入
产品 helper 生成 oracle。需要无理数的标准差并不使所有步骤精确有理化，也不允许将
数值误差冒充业务不确定性。未闭合政策或实现精度不达标的必需格阻塞。

### 2.3 连续区间：原完整网格与条件消费

`time.runs@v1` 直接消费保留完整时间网格的数值 Relation 及 BoundPredicate，
不依赖 DeviationResult。每个非时间坐标元组为一条序列，时间 authority 从原域取得；
不再输入另一套 grain/time_axis，不对纯 Entity 或仅有时间列的任意关系开放。

- 必需输入是原完整格及每格对应、窗口/时区/日历/覆盖、条件全部依赖与其有类型键。
  物理缺行、重复坐标、缺覆盖或 receipt 是违约；合法缺口只能是原格映射中明确保留的
  不可评估格及原因。v1 拒绝不完整边缘格。普通 where 删除时间格后再 runs 必须拒绝。
- 普通数值谓词遇到所需非 Defined Cell 产生 unavailable 并保留原因；状态谓词按自身
  定义求值。复合条件检查所有依赖，不以短路隐藏 unavailable；类型、单位、对应和完整性
  错误仍是硬失败。冻结 true/false/unavailable 分类器，不改普通 where 的严格政策。
- 按原网格邻接枚举全部格，false/unavailable/已登记缺口打断段。输出每个最大 true 段，
  `[首格.start, 末格.end)`，count 为格数，duration 为实际边界差；DST 日和认证日历
  不能用 count×24h 或 retained-row 位置代替。
- 身份绑定原序列、网格、条件输入/版本及起止格。保留区间→格映射、左右终止依据，
  区分 false、unavailable 与观察范围边界。范围结束不能作为业务异常已结束的证据。
- 实现跨批次运输未闭合段与邻接状态，不以批次尾结束区间。后续 where/rank/table
  选择区间而不重新分段；count 接现有数值 rank，duration 接已有单位化筛选。
- 单侧条件分别表达高/低侧；双侧条件允许相邻异号格同段，不根据一个峰值给整段标方向。
  空结果仍保存全格分类计数；全 unavailable 与完整评估后的零命中必须可区分。
- 只有原序列保留实际 SubjectBinding 时才运输区间的主体像；全局 Time 域无主体，
  不能 members。该映射的多实例→唯一 Subject 像遵循既有集合规则，不能改变区间重数。

当前 `core/time_grid.py` 绑定的是具精度声明的完整格。R8.1 检查实际网格部件如何随
Numeric/Difference/score 运输并进入 Artifact；有 grid metadata 而无每格映射不足以准入。
修复精度缺口须由时间 owner 接受，不在 runs 中猜测本地时间、折叠 DST 或重建当前日历。

### 2.4 相关：共同输入、每 pair 域与 lag 方向

接收者加 others 为 2–16 个互异量，按请求顺序生成完整两两组合；量身份而非显示名决定
互异性和 pair identity。每输入必须有共同可验证的观察实例/坐标、完整域和对应依据，
不能只核对行数、Entity 定义或列名，不能隐式 inner join。

- Entity/分类行是统计单位，时间行按点配对，分类×时间在各完整元组序列内配对。
  scalar 不产生相关。冻结复合键、实测唯一性检查与声明依据，来源独立捕获不能假装
  同一个共同成员实现。多量输入复用明确共享节点，不从旧多量容器恢复任意字段。
- `lag_range=None` 为零 lag；显式 range 只对完整时间坐标准入，Entity/分类连
  `range(0, 1)` 也拒绝。+k 为左侧 t 与右侧 t+k；使用 grain/时区/认证日历的原坐标，
  不按幸存行顺序平移，不跨分类序列配对。请求顺序不因词典排序改变方向。
- 对齐后只做 owner 规定的 pairwise Null deletion。保留 input、matched、boundary-drop、
  null-pair、complete-pair 计数，满足 `null+complete=matched` 和 `input−matched=drop`。
  Unknown/Undefined 不能伪装成 Null；非有限/重复/错域等硬失败不能作为丢对处理。
- Pearson、平均秩后 Pearson 的 Spearman、Kendall tau-b 使用唯一方法 owner。
  Spearman 对每 pair 的完整有效对总体求秩，含并列平均秩；不得每批求秩后合并系数。
  Kendall 不新增二次复杂度 SQL pair join 来绕过本地准入。
- 少于两完整对先分类 insufficient_pairs，再判 constant_a/b/both；不能发布 NaN
  代替状态。每 pair/series 至少一个有效 lag，否则整次拒绝；所有候选及无效状态保留。
  selected 按最大绝对系数、最小绝对 lag、最小有符号 lag 唯一选择，不只发布胜者。
- 固定 coefficient/selected 视图，where 不重算相关或重选 lag，search_summary 保持原
  pair×lag×series scope。系数当前行 summarize 只是这些系数的描述统计，不能 rollup
  为合并原观察后的相关。pair 不携带可用于选人的原 Entity identity。

当前旧 owner 的 4096 pair/lag/series ceiling 及来源 Entity pair 准备约束在 R8.1 与
新图现状逐项核对并归入唯一方法 owner；候选数量界是独立方法契约，不扩展为输入行数、
解码字节或工作区内存配额。完整本地输入的成本仅作观测，不作为容量准入条件；不新增
静默截断、采样或失败后 fallback。既有 R4 fixed Entity Spearman 有独立已接受契约，
不得照搬旧 Dataset 的 Entity 收集限制将它撤销；新增方法/形状分别冻结可运输的输入部件、
身份披露和路线。固定准入缺必要部件时早拒绝，不能从 lineage 回源补对。

### 2.5 预测：完整训练域、未来格与名义区间

沿用闭合 `mv.periods(1..1000)`、`mv.naive()`、`mv.drift()`、
`mv.seasonal_naive(periods=s)`，s>1，默认 naive，interval_level 为有限 `(0,1)`。
输入为一个时间量或分类×时间量，各序列有相同完整连续历史和获准未来延续。最短历史为
naive 2、drift 3、seasonal s+1；所有所需值 Defined/有限，Null/Undefined/Unknown、
重复、缺格和部分格拒绝，不插补、不跨序列池化。

[Forecast contract 的规范公式](2026-09-01-lazy-analysis-typed-operators-design.md#forecast)
是 `normal_residual@v1` 的继承依据；R8.1 迁入当前 owner 后实现只消费该版本：

- naive 重复末值，方差随 h 增长；seasonal 重复最后一季，方差按季节步数增长。
  两者创新不去均值，恒定非零创新必须产生非零估计。
- drift 外推首末平均增量，创新只去该增量；自由度 n−2，并传播平均增量估计不确定性。
  不用对 levels 回归的残差或第三方库默认预测公式替代。
- 每序列保留模型、训练键/时间域、创新计数、正自由度、方差/精度、未来 coordinates、
  horizon ordinal、区间定义和假设。缺方差不变零宽区间；零方差仅所有所需创新 exact zero
  时合法，不以 epsilon 夹零。任一 horizon 值/边界不可计算则整次失败。
- prediction 为 ModelPrediction，lower/upper 为 PredictionIntervalBound，三视图同一
  未来域。where 同步限制三视图，prediction 可 rank；描述统计不能赋予原量可加性，
  逐点上下界相加不是总量区间，不因 NumericRelation 类型恢复旧 Metric 执行能力。
- 披露零均值、不相关、恒定有限方差创新及正态预测误差假设；drift 另有参数估计传播。
  这是未来观测的名义预测区间，非均值置信区间。完整历史、执行通过和代数规则不证明
  假设成立或实际覆盖率，不新增经验校准/自动模型选择承诺。

int64/float64/Decimal 的差分、中间创新、点值/区间表示、有限性和舍入随 F08 冻结，
现有 helper 的精度损失检查作为反例线索，不能把其 float 转换自动视为目标 Decimal 资格。
未来格必须在执行/冻结部件中取得，断源恢复不得读取当前 Semantic calendar 修补。

### 2.6 部件运输、实际 K 与早拒绝

| 结果/视图 | 必需保留 | 可公开的实际续算 | 明确拒绝 |
| --- | --- | --- | --- |
| Deviation | 全原域/量、分区与拟合绑定、状态计数、中心尺度/分支、原行映射、版本/数值政策 | 固定四视图、结果 where、合格数值 rank/table、observed 的真实主体像；新评分是显式新节点 | 下游选择后重拟合；score/reference/deviation 原量 rollup/attribute |
| TimeRun | 全原格/条件状态及 scope、原序列、两端/终止依据、区间→格和实际 Subject parts | 固定四视图、结果 where、count rank、duration 筛选、table、有依据的主体像 | 无完整格重新分段；全局时间段选人；Duration 排名扩张 |
| Association | 有序量/pair、每候选坐标/计数/状态、lag/选择版本、原 search_summary、输入绑定与已承诺配对状态 | 固定 coefficient/selected、结果 where、合格 rank/table 和系数行统计 | 筛选后重选 lag；系数 rollup；从系数重建 Entity 域或因果 |
| Forecast | 原训练域/模型/创新与方差依据、未来格、horizon、区间/假设/精度、同域三视图 | 固定 prediction/lower/upper、结果 where、prediction rank/table、获准行描述统计 | 区间加总；observed 身份；离线补日历/训练或自动重新拟合 |

K 是实际冻结 parts 和条件允许的动作，不因“同族结果”全部开放。筛选时主表与各字段
视图同步，拟合/分段/search/训练 authority 保持原 scope；完整性承诺仅在前提仍成立时
运输。新对象必须有 bounded single-line repr、确定性 show 和反映此表的 contract。
fixed 缺必要 parts、版本/输入错配、混合现场来源和固定捕获、跨 Session 等在能够静态
判断时于来源读取及 Run 分配前拒绝；数据依赖检查在注册执行阶段履行并原子失败。

### 2.7 状态与结构化修复

新执行错误继承 AnalysisError，遵循共享模板及具体字段，不用通用 ValueError、自由
reason 字典或没有修复的 NaN。修复建议从实际输入、部件、可用路径和资格产生，不维护
硬编码候选清单。R8.1 将下表落实为状态原因或对应 typed error 的闭合变体：

| 情形 | Expected / received 的必要事实 | 状态或具体下一步 |
| --- | --- | --- |
| 评分不可用 | method、分区、原域/有效 n、尺度分支、原 Cell 原因 | 按契约保留 Undefined score 及计数，不将 n<2/零尺度统一抛成执行失败；show 披露有效域 |
| 输入/字段错配 | exact receiver/dependency identity、完整键域与实际输入/Session | 使用同一接收者的 owned view 和合格对应重新构造；不按列名自动对齐 |
| runs 不完整 | 必需 grid/每格映射、实际缺失/重复/partial 或被选择删除的格 | 对原完整网格构造 runs，把区间筛选放在分段之后；fixed 缺部件明确拒绝 |
| 相关无有效候选 | pair/series、lag scope、complete/null 计数及不足/常量状态 | 修复原观察或显式改选有足够有效变化的输入问题；原子拒绝，不补系数零 |
| 预测不准入 | model/最短历史、实际训练/未来格、状态与自由度/方差 | 提供符合模型的完整历史和认证延续；不建议隐式插补或替换模型 |
| 类型/数值未资格 | method/version、输入/输出精度、所选 route 与实际缺项/溢出 | 指向当前已注册的具体资格或原数值问题；必需格同时保留阻塞，不默默 float 化 |
| 候选数量界/截止时间 | 独立方法候选数量界或统一 deadline、实际候选数/阶段和资源状态 | 按触发的契约显式调整问题后重新执行；不以输入行数、字节或内存估算拒绝，不静默裁剪完整域 |
| 恢复/部件损坏 | Artifact、part/version/key/scope/receipt 的期望与实际 | 拒绝原恢复；按可取得的原来源表达式显式创建新捕获，不能修补旧 Artifact |

## 3. 图、执行、发布与恢复的实施接缝

### 3.1 一条图与来源准备后本地消费

当前责任位置是 `core/{model,rules,graph,time_grid}.py`、
`methods/{semantics,builtin,registry,physical,local}.py`、`compiler/graph_plan.py`、
`compiler/graph_lowering.py`、`materialization/graph_*` 和 `public_dsl.py`。
按职责扩展现有节点/封闭参数/签名，不预建 `relations/` 空包，也不让新方法借旧
`operators/registry.py` 的 family dispatch 执行。

1. Logical 构造绑定全部输入、分区/条件依赖及时间 authority，纯准入零业务 I/O。
   方法注册分别拥有语义和 exact physical qualification；无合格路线时解释缺少哪项资格。
2. Source 路线预先选择 Ibis 准备→schema-bound exchange→本地完整计算；合格原生
   Pearson/Spearman 单列 Ibis 路线。编译、读取或计算失败不重选路线。
3. 完整格不能优化为源端 WHERE 只读 true，拟合不能优化为只读后续 Top-K；shared
   input、拟合节点和必需部件在同一 DAG/Run 内只实现一次。记录真正 reader/submit 次数。
4. Artifact 路线经 receipt/schema/key/parts 校验后进入 pandas/数值方法，禁止
   Artifact→DuckDB、当前 Semantic 加载或 source lookup。源依赖再次顶层 execute
   获得新 realization，固定续算按 exact execution identity 命中。
5. 核只消费 caller-owned 有类型数据和参数，不连接来源、不分配 Run、不发布状态；
   Runtime 统一排序阶段、校验、取消、资源和事务。数值方法无自身 cache/executor。

现有 `graph_plan.py` 对本地 predecessor 有限定放置规则。仅将 deviation/runs 名称
加入 allowlist 不能兑现新链：须同时完成依赖收集、完整交换、local consumer 执行、
Subject/格映射、字段视图与输出恢复，禁止对 local 结果再次 source lowering。

### 3.2 评分后选人继续观察与区间后的显式下一轮

R8 必须实际执行 `change→deviation→where→observed.members→observe→summarize`。
复用 R7 已建立的 source-prefix/prepared observation 责任：同一 Logical DAG 在开始
本地选择前准备下一观察所需的全部来源贡献及映射，之后只本地限制主体/归约；不得本地
score 选人后重新开源查询、上传 Subject 集、把 materialized members 与 live Metric
拼 mixed，或绕回旧 Candidate membership。

这项是 R8.2 的公开出口，F11 冻结准备 identity、路径/时间、完整复合主体键、共享依赖
与费用，而非到 R8.5 才发现续算不可执行。无法给出预准入完整准备路线时精确阻塞该链。

TimeRun 的 start/end 是固定时间值及其依据，agent 可读取后显式选择下一轮 time_scope，
再 observe/compare/attribute。新一轮来源执行产生新 realization，并保留原区间 Evidence
关联；读取边界不自动创建查询或授予时间回源。仅当序列保留真实 Subject 映射时才验证
区间选人路径。普通 source refresh 不改变旧 score/区间的拟合与分段 authority。

### 3.3 完整输入成本、资源与失败

沿用现有 Runtime 的统一 execute deadline；R8.1 核对其覆盖所有新阶段、验证、数值
计算和发布前检查。按架构 §6.3，不新增输入行数、解码字节或工作区内存配额，也不以
数据量或容量估算作为方法准入条件。相关候选 ceiling 按独立方法契约核对；执行超时、
读取或计算失败按既有异常与原子发布契约处理，关闭流并释放已拥有的资源，不自动抽样、
只取首批、改近似或拆分为另一个统计问题。

完整 MAD/排序求秩、Kendall、forecast 历史及 runs 映射须记录实际输入行/列/字节、
Arrow/pandas/NumPy 同时持有的峰值成本、准备和核时间、必要输出/parts 大小。批次模式
只在算法保持全局定义时注册：Spearman 不能分批求秩，runs 可以运输未闭合段；来源准备
的分批不等于数值核流式能力。资源报告不扩大方法准确性或支持矩阵。

校验错误、超时、取消、读/close 错误、跨批次坏 schema 和晚到结果必须关闭资源且无
部分 Artifact/Evidence/Findings 成功。发布前/后与不明提交按唯一 Store 协调；已经持久
提交的成功不能因随后超时改写为失败。固定 exact hit 也要验证当前 receipts/parts。

### 3.4 Evidence/Findings 与 Store 7

Deviation/Runs 保存算法事实、输入 scope 和不可评估原因到共同 Evidence，评分或命中
不自动生成“原因已确认”的 Finding。Association/Forecast 已有描述/预测 Finding 契约，
迁入新图时保留其能力；不能以 graph 当前仅支持 funnel extractor 为由静默清空。

R8.1 在 Runtime owner 冻结 producer/state/extractor/policy 版本、closed body、输入/Artifact
绑定、eligible/emitted/truncated、全序及 cap。当前 graph policy 仅闭合 funnel 两个
producer，扩展须使用精确封闭变体，不增加万能 payload 或旧 family codec 转接：

| 生产者 | 保留的 Finding 义务 |
| --- | --- |
| Association | 描述系数、pair 的量身份/单位/近似、统计单位、lag/计数及原搜索 scope；沿用 valid 候选资格与按 abs(coefficient)/canonical key 的有界排序，不擅自改为只发 selected |
| Forecast | 每未来点的模型、horizon、训练/自由度/方差依据、区间 level/方法/假设；按原 canonical key 的有界发布及完整未来域校验，不用总体 variance range 掩盖某序列损坏 |
| Deviation/Runs | 默认空 Finding 集仍受 exact digest/count 校验；Evidence 可披露统计和条件事实，不能升级成因果、显著性或推荐计划 |

共同 cap 及已有闭合 body 的具体接受规则在 R8.1 核对并冻结，产品不复制另一份常量。
Artifact、所有 RequiredParts、Evidence、Findings 和 terminal Run 在同一事务发布。
公开 digest/page/single read、断源新进程、exact hit 都校验 body、input/Artifact binding、
完整 set digest、排序、截断和版本；不保留 Association/Forecast 专属恢复 authority。
新量身份若超出旧仅 Metric Ref 的 body，先在 owner 接受封闭扩展，不伪造 Metric 身份。

主表与必要状态分别有 schema/version/key/row-count/receipt，恢复验证拟合 scope、
每格/条件、pair/search 和 training/future 的交叉一致性。不能只读显示列就声称相同 K，
不能重跑算法或加载当前定义“修复”损坏。已筛选派生 Artifact 保留原 scope authority，
但不得按生产者的完整输出域误校验为未筛选结果。

## 4. R8.1 契约与迁移冻结清单

R8.1 只完成文档/静态冻结，不授予任何新 Runtime、backend 或 wheel 资格。
实际产物为 R8 migration ledger (historical record in Git history)、
consumer snapshot (historical record in Git history) 和
evidence index (historical record in Git history)。记录实际基线、输入 hashes、
生成方式和静态验证；所有新执行资格仍为 planned 或明确 blocked。

| ID | 冻结项 | 唯一落点与出口 |
| --- | --- | --- |
| F01 | C14.a1/a2/b/c、主动退出五法、必需与研究边界 | 接口 owner/能力 ledger；新资格要求不能删除未通过的必需格 |
| F02 | 具体 Logical/Materialized 重载、export 与 Result family、owned fields | Python design；静态类型正负例、唯一公共入口，不增加同义 namespace |
| F03 | Deviation 输入/分区、拟合域、每状态表、n/中心/尺度/分支 | Operators owner；四视图同原身份与完整状态，筛选保留原 scope |
| F04 | 两个评分方法的 int64/float64/Decimal 算术、舍入、精度/溢出 | Operators/physical qualification；逐类型独立 oracle 和阻塞项 |
| F05 | Runs 完整格、每格对应、条件依赖、true/false/unavailable | Operators/time owner；部分格/缺行/重复与合法 unavailable 分离 |
| F06 | TimeRun 身份、最大段、终止依据、Duration、区间→格/Subject | Operators/parts；范围截止语义、批次续接与后筛选不分段 |
| F07 | Association 2–16 互异输入、共同域、有序 pair/lag/选择/计数 | Operators owner；现有 R4 Spearman 承接，新形状与数值矩阵单列 |
| F08 | Forecast 三模型、normal_residual、训练/未来格/自由度及数值 | Operators/time owner；规范公式、zero-innovation、逐序列依据与全 horizon 原子性 |
| F09 | RequiredParts、闭合状态 schema/version、字段和选择 PartTransform、实际 K | Core/methods；四 Result/view 的来源、固定、筛选和恢复矩阵 |
| F10 | exact implementation keys、来源/固定路线、共享/新求值及 precision | Compiler/Runtime；预选路、构造零 I/O、没有旧 registry/fallback |
| F11 | 评分/区间后选人及下一观察的 source-prefix、prepared inputs | Runtime/graph placement；完整依赖在 local 前准备，复合键与输入身份绑定 |
| F12 | exchange、统一 deadline、资源/原子失败、identity/receipt/恢复 | Runtime；独立方法 candidate ceiling、本地完整输入成本观测、无容量准入及 exact hit 校验 |
| F13 | Evidence/Findings 封闭 body/policy、全序/cap/输入与 scope | Runtime/evidence owner；公共读取/事务/冷恢复，不保留旧 codec 双读 |
| F14 | expected/received/repair、repr/show/contract、Help/预算、CLI/中英文档 | Native disclosure owner；独立 reachability/drift/budget 与英语最小可执行示例 |

consumer snapshot 必须从实际 import/call/注册/导出/Help/Store 分支和全部测试 node 反查。
记录 AST/static 证据与动态可达性待验证项；不能凭文件名或一次 rg 零命中宣布删除闭合。
必需资格每格给稳定 ID、method/version、type、domain/parts、route、physical form、
time profile、required 标记及证明类别。后续 bounded passes 只填实际格，不缩减冻结集合。

## 5. 工作包顺序与独立出口

主链为 `R8.1 → R8.2 → R8.3 → R8.4 → R8.5 → R8.6`。R8.4 内先扩大 Association，
再接 Forecast，复用已闭合的图/视图/恢复；各自独立记录通过与阻塞，不以一项替另一项。
每包将 owning specs、类型/Help/dynamic guidance 和适用中英文例子随实施同步，R8.5
只负责最终收口，不能长期公开半成品或旧兼容入口。

| 工作包 | 实施责任 | 必须满足的出口 |
| --- | --- | --- |
| R8.1 契约与迁移冻结 | 完成 F01–F14，M01–M18 逐调用方账本，V01–V20 必需格、静态快照和原测试处置；处理旧 owner 与新图冲突 | 必需方法无未归属契约；所有待决数值/部件/Findings/路线明确，不批准占位 export；只有静态证据 |
| R8.2 偏离评分 | 新封闭规则、两方法、数值/Cell、四视图与 fit parts；来源准备/固定核、Store 7 恢复；F11 评分选人继续观察 | 两方法各有完整 public execute、断源与同域 where/rank/table；独立算术/状态 oracle；真实 source-prefix→local→下一观察，非私有 kernel 完成 |
| R8.3 连续区间 | 网格/条件 classifier、最大段和 Duration、两端及映射 parts；四视图/筛选；跨批次状态；来源/固定与恢复 | 直接业务阈值和 score 阈值都执行；true/false/unavailable、DST/日历、Subject/无 Subject、筛选违约及离线不分段证明 |
| R8.4 相关与预测 | 扩大现有 AssociationResult 到三方法/多量/lag；ForecastResult/三模型；input/future parts；非空 Finding 发布/读/恢复 | C14.b/c 全部闭合目标逐方法执行；双路线持续对照、每 pair/序列 oracle、错误和冷恢复；J4 及公开统计组合无旧 Dataset 链 |
| R8.5 公开切换与退役 | 删除 discover/Candidate 与旧 Association/Forecast 独占链；逐符号收口 SQL/helpers/codec/exports；独立 disclosure/typing 和完整中英 latest 示例 | 新路径可执行且旧入口不可导入/发现/恢复/隐藏调用；M01–M18 有替代、不可达、物理删除或真实剩余 owner；不留转发 alias |
| R8.6 本地验收与交接 | 冻结矩阵与 A11 全链、关联 A02/A04/A07/J1–J4；source/fixed/cold/exact hit、损坏/资源；同 candidate wheel；广域门禁 | 必需本地格与安装证据齐全；失败/skip/阻塞独立记账；R9/R10 接到真实路线/形态缺口，不授六后端/Agent/发布资格 |

R8.2/3 各包就接入真实 Runtime/Store/恢复；不能先提交仅可导入的类或私有核，再把所有
公开续算推迟到退役包。旧入口删除以对应新 consumer 实质接通为条件，原主动退出启发式
无需伪造等价替代。R8.4 扩大方法时持续保留已接受 R4/R5/R6 图路径和身份/恢复边界。

## 6. 迁移、删除与共享消费者清单

以下为起草时确认的定位清单。R8.1 按实际消费者补全精确符号、测试 node、替代证据及
删除条件；不重建已经由 R6/R7 删除的模块。仅相关/预测代码存在不代表当前 source 准入。

| ID | 当前入口/消费者 | 新 owner、处置与删除门禁 |
| --- | --- | --- |
| M01 | `operators/discovery.py`、`candidate_dataset.py`、旧 `.discover` 与五方法 | NumericRelation.deviation/runs + 现有 where/rank/members/attribute；删除 namespace/五包装，无 redirect |
| M02 | `candidate_contracts.py`、`candidate_values.py`；point/entity/window/period 定义 | Deviation/Runs 唯一语义/核；保留均值/MAD/状态反例，删除旧绝对分数/阈值/峰值包装和 period_shifts 隐式滑窗 |
| M03 | `driver_axes.py`、`driver_contracts.py`、`driver_values.py`、`driver_expansion.py`、`compiler/driver_candidate.py` | 按明确轴使用 R6 attribute；删除 50% 前缀及排轴评分，不构造另一 driver 执行链；共享归因符号按其余调用方处理 |
| M04 | `compiler/entity_candidate.py`、`compiler/driver_numeric.py` 的实际调用方及数值安装 hooks | 合格 Ibis 准备/统一 local methods；按 SQL ledger AN01/AN15 实测消除 macro/自定义 compiler 通道，不将 BIGNUM helper 直接搬入新评分 |
| M05 | `public_dsl.py`、graph composition/relation、`graph_spearman_execution.py`、`methods/local.py` 的双量 Spearman | 扩展既有 Association 图 owner；迁入三方法/arity/shape，合并同方法重复算术且持续保留 J4 与 fixed 契约 |
| M06 | `operators/correlate.py`、`association.py`、`association_contracts.py`、`association_values.py` | 抽出保留方法和配对/选择契约归统一 methods；删除 Dataset producers/handles/payload，不能调用旧 runtime 伪装接通 |
| M07 | `compiler/correlation.py` 及 source pair/native reduction consumers | graph lowering/准备 owner；按 exact shapes 保留 Ibis 构造能力与必要校验；无手写 SQL、执行失败重选或身份强转 |
| M08 | `operators/forecast.py`、`forecast_dataset.py`、`forecast_contracts.py`、`forecast_values.py` | ForecastResult/统一 methods；保留工厂及规范模型公式，删除旧 Dataset 契约/输入 payload/执行重复 owner |
| M09 | `candidate_codec.py`/`candidate_publication.py`、`association_codec.py`/publication、`forecast_codec.py`/publication | graph method/part codec、统一发布和恢复；先接合法视图/证据再删除独占 reader/writer，不双读旧 generation |
| M10 | `datasets/descriptors.py` 的 candidate/association/forecast evidence；storage/source preparation 的 family bits | 逐字段迁到 checked typed signatures/parts/Evidence；共享 scalar/input-binding helpers 由真实剩余 owner 接收 |
| M11 | `operators/registry.py`、compiler nodes/normalize/lowering/placement、source admission | 唯一 methods registry 与 qualification keys；删除旧 payload/producer/family/legacy migration dispatch；不能仅清理导出 |
| M12 | `materialization/dataset_execution.py`、execution/source_stage/local_stage 的候选/相关/预测分支 | 统一 graph Runtime 依赖收集、exchange/local execution/resource；旧消费者清零后删除分支，保留其他阶段真正共用服务 |
| M13 | `graph_plan.py`、graph observation/preparation/local execution、selected membership | F11 prepared input 与新结果实际 Subject/格运输；扩大合法 local 链须实际执行且没有 source-after-local/mixed |
| M14 | `graph_protocol.py`/exchange/storage/snapshot、`public_dsl.py::wrap_materialized`、Session artifact read | 新 Result/selected/view 的 exact recovery 与 K；不能按数量值、通用 dict 或旧 codec 恢复；跨 Session 早拒绝 |
| M15 | `graph_findings.py`/store/publication、`evidence/_finding_registry.py`、`finding_values.py`、公开 reads | F13 封闭 producer/policy/body 与完整 scope；保留 Association/Forecast Findings，删除旧 family read dispatch；统一原子校验 |
| M16 | `analysis/__init__.py`/`_public.py`、native Help/_capabilities/introspection、CLI | 唯一新 exports/signatures/targets；删除 Candidate/旧 Dataset discovery leaves；现态 contract/repair 和预算独立验收 |
| M17 | `docs/api/analysis.rst`、owning specs、Runtime coverage、`site/src/content/docs/{docs,zh-cn/docs}/latest`、packaged skills | 实施包同步 API/中英示例/当前说明；历史 release 文字不冒充当前入口。skills 若需修改先完成具体 proposed diff 并取得仓库规定的明确授权 |
| M18 | `tests/test_lazy_{candidate,entity_candidate,driver,correlation,forecast}_*`、workers/fixtures、wheel staging | 逐 node 保留独立反例、替换公共组合或主动退出 heuristic；新 public oracle 成立后删除旧 harness；远端旧测试不授 R9 资格 |

每项分别记替代执行、旧调用不可达、物理删除和剩余共享 owner。`attribute_values`、
observation/temporal、adapter 生命周期、input-bindings 和通用 evidence 不能按文件名
整批删除。AN01/AN15、相关/预测准备的 temporal 路径与 adapter 控制各自绑定实际
SQL caller；既有特定 metadata 例外不授权新 Analysis SQL。静态扫描补实际签发/提交审计。

测试处置分三类：保留可击穿新方法的算术/状态反例；用新公共组合替换旧家族流程；主动
退出只证明旧 wrapper/default/heuristic 的断言。旧 Candidate 结果不作为新 signed score
或 run identity oracle，旧 driver 排轴分数也不成为 R6 contribution 排序的预期。

## 7. 验收矩阵、独立 oracle 与公开旅程

R8.1 将下表展开为不可变必需 requirement IDs；R8.2–R8.6 每次填实际命令、method/type/
shape/route、输入和 candidate hash、oracle、退出码和附件位置。测试数、AST 数或容差
接近不能替代定义/域/parts/K 证据。本文无已通过项。

| ID | 覆盖与独立预期 | 主要责任 |
| --- | --- | --- |
| V01 | 具体签名/Ref/所有权/闭合参数的类型正负例，跨 Session/混合/同名不同域拒绝；构造/Help/计划零业务 I/O | R8.1–R8.5 |
| V02 | zscore/MAD 的独立均值/总体方差/中位数/尺度分支 oracle；相同数值 Time/Entity 分数一致但身份/K 不同 | R8.2 |
| V03 | 空/无有效样本、单样本、常量、MAD=0 fallback、全部四 Cell 和各状态数；非有限/坏身份/覆盖硬失败 | R8.2 |
| V04 | int64 大数近值、float 极值/消去、Decimal precision/scale；中心/差值/尺度/score 的输出、舍入/误差/溢出 oracle；禁止无定义强转 | R8.2/R8.4 |
| V05 | 显式分区/Null 类别/复合键、乱行/跨批次；deviation 后选择不重拟合，先选择拟合新域；共享 reader/核次数 | R8.2 |
| V06 | 全格枚举 runs：true,false,true 与 true,unavailable,true 均两段；直接 -20% 业务阈值命中而零尺度 score 不可用 | R8.3 |
| V07 | 单侧/双侧异号、所有依赖非短路分类、状态谓词、空命中/全 unavailable；单位/错域/非法缺口硬失败 | R8.3 |
| V08 | 网格/partial/missing/duplicate、首尾/跨批次长段、DST 23/25h 与认证日历、多个序列；区间→格/两端/实际 Duration oracle | R8.3 |
| V09 | 普通 where 删除格后 runs 拒绝；后筛选不分段；有/无 Subject 的合法 members/早拒绝；Duration 无排名扩张 | R8.3 |
| V10 | 2/3/16 量完整 pairs，重复量/第17量/错域拒绝；Pearson 独立中心化和式、Spearman 独立平均秩、Kendall 独立成对计数 | R8.4 |
| V11 | Entity/分类/时间/分类×时间；正负 lag 方向、日历邻接、边界/Null 丢对、不足/常量；所有候选与 selected tie oracle | R8.4 |
| V12 | 同一方法向量的 qualified Ibis 与完整本地路线对照；失败不 fallback，系数范围/状态矛盾、独立 pair candidate ceiling 拒绝；不因输入行数/字节/内存估算拒绝 | R8.4 |
| V13 | 三模型规范点值/创新/自由度/horizon variance 和独立正态分位 oracle；恒定非零创新、exact-zero、drift slope 与 seasonal 多季 horizon | R8.4 |
| V14 | 最短历史、level/horizon/season 闭合边界、全 Defined 完整训练/未来域；跨序列不池化，缺/重复/partial/坏方差/非有限原子拒绝 | R8.4 |
| V15 | 四 Result/views→where/rank/table 的域/字段/scope/K；系数或预测描述统计不冒充原量；共享多字段无重复拟合，非法 rollup/attribute/区间加总拒绝 | R8.2–R8.5 |
| V16 | A11 与 A02/A04/A07/J1–J4 公共组合；评分选人后真实下一观察、区间边界后的新一轮、两单轴 vs joint 归因，费用/来源身份可追溯 | R8.2–R8.6 |
| V17 | 来源重复求值/共享节点/fixed exact hit；独立 producer/continue/cold 三进程，断源/禁止当前 Semantic/DuckDB；恢复值/状态/域/parts/K | R8.2–R8.6 |
| V18 | 每个必要 part/version/receipt/key/scope 的独立损坏；非空 Findings body/digest/排序/cap/输入与空集；取消/超时/close/晚到/事务失败资源原子性 | R8.2–R8.6 |
| V19 | exports/Help reachability/budget/drift、动态 K/repair、repr/show、CLI/API/中英例子；旧入口/import/registry/codec/SQL 提交不可达与物理退役 | R8.5/R8.6 |
| V20 | 同 candidate 非 editable wheel、site-packages origin/依赖/hash/poisoned PYTHONPATH；独立公开 A11/A04、table/Parquet 与所有新 Result 冷恢复 | R8.6 |

独立 oracle 不导入被测数值核、不通过旧 wrapper 求新期望、不只比较两个共用 helper 的
实现。预期身份/完整域用原事实枚举；MAD/相关/预测的精度容差在冻结数值 owner 中有依据，
不为通过测试逐例放宽。每个方法还须有持续双实现防漂移，只有一条资格路线时记录该限制，
不能虚构第二路线已通过。

### 7.1 A11 必需公共组合

1. 从正式 datasource/semantic 项目以 `import marivo.analysis as mv` 和
   `session.members` 开始，观察两期并比较。Entity 变化做 MAD/zscore，状态 where
   后显式数值 where，经 observed.members 继续观察另一时期并 summarize；运行一次
   完整 Logical DAG，审计全部 source preparation 在 local selection 前完成。
2. 完整日格相对变化直接用业务阈值 runs，另从完整值域 deviation→score.runs；包含
   恒定下降、不可评估间隙和异号双侧段，读 start/end/count/duration。显式下一轮
   time_scope 再 observe/compare/attribute；分数和格数不作为收入归因输入。
3. 对同一原变化分别 attribute Region、Channel，另做 joint；独立核对各自原目标与
   basis，不把两单轴贡献相加，不用旧 driver_axes 集中度排序当预期。
4. 三量相关分别执行三方法，含 time lag/分类×time、pairwise Null/ties/invalid lag。
   where(selected)→coefficient.rank/table 保持原搜索 scope；保留 A04/J4 的明确
   Spearman 无 lag 旅程，检验没有隐式改为 Pearson。
5. 三模型分别预测未来格，where(prediction)→同域 prediction/lower/upper table；
   检查跨 horizon 方差、训练条件/假设和非法区间加总。逻辑视图组合在一次 execute 完成。
6. 每类固定结果都在独立继续计算进程和断源新进程恢复，保留原 scope/parts/Findings。
   真实新数值方法的 fixed 执行与仅 where/rank/table transport 分开计数，不能用成功
   transport 代授未执行的 fixed kernel。恢复后相同表达式 exact hit，再做损坏拒绝。

公共脚本通过是 DSL/Runtime 证据；真实 Agent 独立解题仍归 R10。Agent 自主选择业务
阈值、方向、指标、轴和后续问题，Help/contract 不注入推荐计划或预写答案。

## 8. 工程门禁、证据与完成判定

### 8.1 按包验证

- Python 改动先跑 narrow `make test TESTS='...'`、
  `make runtime-test TESTS='...'`、`make typecheck TYPECHECK_TARGETS='...'` 与
  `make lint-agent LINT_TARGETS='...'`，随后公共/共享行为收口跑 `make check-agent`。
  当前旧测试是迁移线索，R8.1 创建新测试后将精确路径写入 ledger；不把尚不存在的
  命令登记为已运行。新增 fixtures/builders/process workers 遵守 `marivo-test-fixtures`。
- 每状态/codec/parts 包含三进程 producer/continuation/cold 与损坏/故障注入。默认测试
  跳过 Runtime 不算 Runtime 通过；static exports/AST/collect-only 不算执行证明。
- API docs 通过 check-agent 的文档阶段或 `make docs-api`；latest English/Chinese
  示例同步，以 `npm --prefix site run build` 验证。产品、测试、代码示例与错误保持英文。
- R8.5 做 import/call/registry/codec/Help 的反向退役 guard，加实际 datasource issued
  Ibis 产物与 driver 提交逐条一致审计；零字符串 grep 不替代运行证据。
- R8.6 用 `make pypi-build pypi-check` 生成一个明确 candidate，按适用 release 标记
  单独执行现有 `tests/test_analysis_runtime_wheel.py` 中扩展后的 R8 安装 gate。
  内部修改后重建 candidate，旧 archive 成功不沿用；不因包构建成功记 wheel 旅程通过。

普通阶段不运行完整 `make release-check`、不启动 MinIO 或进行发布。安装 gate 单列
wheel-origin、archive 内容/依赖及 fresh-process 冷恢复证据，外部 temporary test root
和 source `PYTHONPATH` 污染拒绝须真实验证。R9 remote/native 全面矩阵和 R10 真实 Agent
不得由本地 DuckDB、source tree 或 installed transport 代授。

### 8.2 记录格式与交接

每包 evidence index 至少记录：实际 branch/SHA/dirty diff、owning spec/input hashes、
依赖/驱动版本、数值/时间/物理形态、method/implementation/route、原完整 requirement ID、
测试命令/退出码/附件 digest、独立 oracle、共享读与资源事实、source/fixed/cold/installed
类别、所有失败/skip/阻塞和最终 disposition。证据可在 ignored 目录，但受版本控制的
索引须说明获取方法和 hash；已经取消跟踪的前序附件不恢复、不伪造、不要求新 checkout
必然持有。不能根据历史数值或测试总数重建缺失资格。

R8.6 交给 R9 每个新 method/version 与输入/类型/时间/物理 form 的六后端目标，来源
准备/原生路线差异、SQL caller 处置、实际 submit/取消/成本与未验证格；交给 R10 同候选
wheel、公开脚本、Help 起点、cold 命令、失败/repair 和真实 Agent 尚未完成的边界。

### 8.3 完成判定

1. F01–F14 有唯一接受 owner，C14.a1/a2/b/c 的必需本地方法/数值/域/parts 目标逐格
   执行；未通过必需格仍是未完成，主动退出旧五法与实现困难的拒绝分开记账。
2. zscore/MAD、runs、三种 correlation 和三种 forecast 均经 public→graph→Runtime→
   Store 7，具有独立 oracle、病态输入、批次/行序检查、固定方法与冷恢复证据。
3. 结果筛选/视图/rank/table 保留原拟合、分段、search/训练 scope；评分选人继续观察
   和区间后的显式下一轮真实执行。实际 K 与拒绝相符，没有 local→source/mixed 绕行。
4. 主表/RequiredParts/Evidence/Findings 的发布、公开读、cold 和 exact hit 使用共同
   事务/校验，损坏、读取/计算失败、超时/取消和不明提交有证据；恢复无隐式重拟合、分段或回源。
5. M01–M18 删除门禁闭合，旧 discover/Candidate/Association/Forecast 独占执行、
   exports/Help/codec/SQL 不能调用或恢复；共享 helper 的每个剩余 owner 明确，无 shim。
6. V01–V20、相关广域门禁、API/site、同候选 installed wheel 通过，并保留原失败。
   Help/类型/现态 contract/repair 与中英示例一致；skills 维持适用或另行取得明确编辑授权。
7. R9 六后端和 R10 真实 Agent/发布有独立交接，不将 R8 的本地完成写成全重构接受。

2026-10-03 初始文档起草仅验证链接、结构、范围和 whitespace；未执行产品测试或授予
运行资格，未编辑 AGENTS.md 或 packaged workflow skills，未提交、推送或发布。


## 9. R8.5 实施检查点（2026-10-04）

实施基线为 `panda`、`f0b1c5930d993338f94a4c415458729f6d67953f` 的干净工作树。
R8.5 独立证据索引 (historical record in Git history)保留全部 171 个原始 ID：
V15 54 项、V19 117 项均通过。449 个符号与 391 个旧测试节点均有当前处置，
209 个移除节点的断言转移没有未验证项，M01–M18 退役门禁全部闭合。
25 个保留的共享 Runtime/远端测试节点未在本轮执行，继续明确记为未验证；
它们的 owner 保留不代表获得运行或后端资格。

公开统计入口已切换到 NumericRelation，旧 Metric/Delta discover 与
Association/Forecast Dataset 专属链、注册、codec、恢复和数值宏安装通道已退出，
不提供转发 alias 或双读迁移。真实 distribution caller、forecast 工厂、共同
Findings 与 scalar/input-binding codec 保留；R4 Spearman 的实现身份、状态、
parts 和 J4 恢复边界保持原契约。类型、Help、动态指导、CLI/API 与中英 latest
九方法示例同步；仅修订已授权的 analysis skill 三处 graph/对象/结果措辞。

紧凑广域门禁通过 5,872 项并保留五项 skip；精确断言 owner 的 Runtime 命令
139 项全部通过。冻结视图、公开 source/fixed/cold、前序路径、中英示例、旧 Artifact
拒绝、独立 oracle、typing/lint 和 site build 的命令、退出码与附件摘要分别记账，
不加总重叠范围。补充回归 201 项通过、4 项失败；四项均在基线源码副本复现，
分别为 R6 固定时间 key 的 blocked 注册与 A08 输入 shape 不一致。未修改这些
前序规则，也未将失败命令用于断言转移通过证据。

R8.1 原始快照和 R8.2–R8.4 资格附件未改变；本检查点不补授前序或全 R8 验收，
不开展 R8.6/R9/R10、release Runtime gate、wheel 或发布。保持既有 deadline、
资源释放与原子失败规则，不增加输入容量准入；未编辑 AGENTS.md、提交或推送。
