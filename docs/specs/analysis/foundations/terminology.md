# Analysis 术语对照表

本文档基于 `specs/analysis/` 现有设计文档整理，用于统一中英文术语。原则如下：

- 代码标识符、Schema 字段名、枚举值保持英文
- 正文优先使用中文；必要时在首次出现处保留英文括注
- 与 Marivo 外部契约强相关的概念，优先保持一词一译，避免在不同文档中漂移

## 核心概念

| English | 中文推荐译法 | 使用说明 |
| --- | --- | --- |
| Evidence Engine | 证据引擎 | Marivo 的证据分层与推断框架总称 |
| canonical abstraction | 规范抽象链路 | 指 `artifact -> finding -> proposition -> assessment -> action proposal` 这条规范分层 |
| canonical object | 规范对象 | 在 canonical state 中具有稳定类型与标识边界的对象 |
| analysis intent | 分析意图 | Marivo 的语义分析动作单位 |
| primitive intent | 原子意图 | 最小、不可再约简的分析意图 |
| derived intent | 派生意图 | 由多个原子意图确定性展开而成 |
| analysis step | 分析步骤 | 执行层步骤；正文可与 analysis intent 对照使用 |
| typed contract | 类型契约 | 强调输入输出类型稳定、可校验 |
| typed schema | 类型 Schema | 文档中的类型定义部分 |
| semantic layer | 语义层 | 面向指标、维度、实体的语义抽象层 |
| semantic metric | 语义指标 | 已发布的 metric 名称与能力定义 |
| semantic dimension | 语义维度 | 语义层发布的切分维度 |
| observation | 观测结果 | `observe` 的产物，也可指 typed observation |
| finding | 事实单元 | 从 artifact 中确定性抽取出的可引用事实；`observation` 是其子类型之一 |
| artifact | 工件 | 完整、可引用、可复现的步骤输出 |
| projection | 投影 | 从工件派生出的确定性压缩视图 |
| provenance | 溯源信息 | 结果的来源、执行与引用链路信息 |
| validation | 校验 | 包含输入校验与语义可辩护性校验 |
| proposition | 命题 | 待评估的结构化判断对象，不自带 confidence 或状态 |
| assessment | 评估状态 | 系统当前对某个 proposition 的判断状态与证据完备度 |
| action proposal | 动作候选 | 面向 agent 的可采纳动作建议，属于外层动作支持接口 |
| priority axes | 优先级轴 | 动作候选的多轴排序维度，如信息增益、成本、紧急性、影响 |
| policy profile | 策略配置 | proposal 生成与排序所依赖的显式策略档位或策略版本 |
| inference | 推断 | 由显式规则把 findings 转化为 assessments 的过程 |
| inference record | 推断记录 | 对一次规则命中、输入引用和输出评估结果的结构化记录 |
| evidence gap | 证据缺口 | 阻止命题被进一步确认的缺失条件或缺失证据 |
| gap requirement | 缺口要求 | `evidence_gap` 中对“缺什么”做类型化表达的 requirement 对象 |
| canonical support object | 规范支撑对象 | 不是主层级对象、但属于 canonical judgment support 的对象，如 `evidence_gap`、`inference_record` |
| canonical analysis state | 规范分析状态 | 面向 agent 的结构化状态视图，包含 findings、propositions、assessments 等 |
| analysis state surface | 分析状态面 | agent 读取 canonical analysis state 的主交互面 |
| context surface | 上下文面 | 围绕单个 proposition / hypothesis 提供局部最小闭包的读取面 |
| state schema | 状态 Schema | 组织 canonical objects 成为稳定读取视图的契约 |
| canonical view | 规范视图 | 由 canonical objects 组织出的稳定读取视图，不引入新的核心证据语义 |
| stable reference | 稳定引用 | agent/UI 可长期持有并回查的对象引用 |
| identity boundary | 标识边界 | 决定对象 ID 是否应复用的语义边界 |
| identity normalization | 标识归一化 | 生成 canonical ID 前对 identity 输入做规范化的过程 |
| lineage | 谱系 | 对对象来源、派生路径和上游工件/步骤链路的结构化描述 |
| session-local | 会话内局部 | identity 或状态仅在单个 session 内稳定，不跨 session 复用 |
| atomic evaluable | 原子可评估单元 | 可被单独支持、反驳、阻塞或升级的最小命题粒度 |
| seed finding | 种子事实单元 | 在 proposition 创建时作为 seed 输入的 finding |
| seed finding ref | 种子事实引用 | proposition 中记录 creation-time seed finding 的引用项 |
| live evidence membership | 实时证据归属 | assessment 当前纳入支持/反驳集合的 live evidence 关系 |
| local closure | 局部闭包 | 围绕某个 subject 或 proposition 做最小必要关联读取的结果闭包 |
| minimal local closure | 局部最小闭包 | agent 做解释和决策所需的最小 canonical 对象闭包 |
| live proposition | 活跃命题 | 仍处于当前 session judgment track、可继续评估或解释的 proposition |
| comparability | 可比性 | 两个观测是否可合法比较 |
| detectability | 可检测性 | 指标与扫描形态是否适合做异常候选检测 |
| forecastability | 可预测性 | 历史序列是否适合做前向预测 |
| attribution | 归因 | 将变化量分配到维度贡献项 |
| decomposition | 分解 | 统计意义上的分解；v1 仅支持变化分解 |
| hypothesis test | 假设检验 | `test` 输出的统计检验结果 |
| anomaly candidate | 异常候选 | 值得进一步分析的候选点，不等于已确认异常 |
| bounded output | 有界输出 | 输出规模可控，适合 agent / UI 消费 |
| truncation | 截断 | 因 `limit` 等策略丢弃部分结果 |
| ranking | 排序规则 | 用于稳定返回 top-k 结果的确定性规则 |
| DAG | 有向无环图 | 派生意图内部展开的执行图 |

## 原子意图家族

| English | 中文推荐译法 | 说明 |
| --- | --- | --- |
| observe | 观测 | 读取语义指标并产出类型化观测结果 |
| compare | 对比 | 计算两个观测之间的类型化差异 |
| decompose | 分解 | 将已定义的 delta 分配到维度贡献项 |
| correlate | 关联 | 估计两个时间序列的统计关联 |
| detect | 检测 | 扫描时间范围并返回异常候选 |
| test | 检验 | 对结构化统计假设做显著性判断 |
| forecast | 预测 | 将历史序列投影到未来时间桶 |

## 常见结果类型

| English | 中文推荐译法 | 说明 |
| --- | --- | --- |
| scalar observation | 标量观测 | 单个数值结果 |
| time-series observation | 时间序列观测 | 按时间桶返回的序列 |
| segmented observation | 分段观测 | 按维度切片返回的结果 |
| sample summary | 样本摘要 | 面向统计检验的摘要型观测 |
| scalar delta | 标量差值 | 两个标量观测的 delta |
| segmented delta | 分段差值 | 两个 segmented 观测逐段对比后的 delta |
| delta decomposition | 变化分解 | 按维度解释 scope delta 的归因结果 |
| pairwise time-series | 成对时间序列关联结果 | `correlate` 的 v1 结果类型 |
| anomaly candidates | 异常候选结果 | `detect` 的返回类型 |
| hypothesis_test | 假设检验结果 | `test` 的返回类型 |
| forecast_series | 预测序列 | `forecast` 的返回类型 |
| finding set | 事实集合 | 围绕某个主题或命题聚合的一组 findings |
| proposition assessment | 命题评估 | 对 proposition 的结构化评估结果 |

## Evidence Engine 分层术语

| English | 中文推荐译法 | 说明 |
| --- | --- | --- |
| artifact layer | 工件层 | 保存完整步骤输出的分层 |
| fact layer | 事实层 | 以 `finding` 为核心对象的分层 |
| judgment layer | 判断层 | 承载 `proposition`、`assessment` 及其支撑对象的分层 |
| action-support layer | 动作支持层 | 生成 `action proposal` 的最外层分层 |
| canonical fact unit | 规范事实单元 | `finding` 的设计定位 |
| canonical judgment object | 规范判断对象 | `proposition` 的设计定位 |
| canonical evaluation state | 规范评估状态 | `assessment` 的设计定位 |
| projection-layer canonical object | 投影层规范对象 | 由 canonical state 投影生成、但仍具稳定契约的对象，如 `action proposal` |
| session state view | 会话状态视图 | session 级主读取视图，如 `SessionStateView` |
| proposition context view | 命题上下文视图 | proposition 级局部闭包视图，如 `PropositionContextView` |
| consumer-facing canonical view | 面向 consumer 的规范视图 | 面向 agent/UI 的稳定读取视图，不改变底层 canonical object identity |
| reasoning object | 推理对象 | agent 围绕其组织支持、反驳与验证的中心对象，通常指 proposition |
| judgment anchor | 判断锚点 | 承接 assessment/inference 的稳定判断对象，通常指 proposition |
| assessment anchor | 评估锚点 | proposition 到 assessment family 的静态挂接点 |
| decision spine | 决策骨架 | agent 默认做判断时优先读取的核心状态组织方式 |
| main read surface | 主读取面 | 回答“当前整体最值得关注什么”的默认状态读取面 |
| backing findings | 支撑事实单元 | state/context view 中作为主事实载荷返回的 finding 集合 |
| focus subjects | 焦点主语 | 从返回的 findings 稳定去重得到的主语索引 |
| artifact ref | 工件引用 | 指向权威 artifact 的稳定引用或最小 lookup handle |
| lookup handle | 查找句柄 | 只负责定位对象，不重复承载完整 payload 或 provenance 的轻量句柄 |

## 时间与范围相关术语

| English | 中文推荐译法 | 说明 |
| --- | --- | --- |
| time_scope | 时间范围 | 统一的时间契约 |
| range | 区间 | 半开区间 `[start, end)` |
| snapshot_now | 当前快照 | 查询时刻即时快照 |
| latest_available | 最新可用点 | 当前稳定可用的最近时点 |
| as_of | 截至某时 | 某个时间点的快照 |
| granularity | 粒度 | `hour / day / week / month` |
| time bucket | 时间桶 | 时间序列中的单个 bucket |
| half-open interval | 半开区间 | 左闭右开 `[start, end)` |
| horizon | 预测期长度 | 未来 bucket 数量 |

## 统计与分析术语

| English | 中文推荐译法 | 说明 |
| --- | --- | --- |
| coefficient | 系数 | 例如 correlation coefficient |
| p-value | p 值 | 显著性检验常用输出 |
| significance | 显著性 | 基于 `p_value` 与 `alpha` 的状态 |
| confidence interval | 置信区间 | `test` 中 estimate 的区间估计 |
| prediction interval | 预测区间 | `forecast` 中未来值的不确定性区间 |
| baseline | 基线 | 对比或检测中的参考侧 |
| expected value | 期望值 | `detect`/`forecast` 中的基准估计 |
| absolute delta | 绝对变化量 | `left - right` |
| relative delta | 相对变化量 | `absolute_delta / right_value` |
| contribution share | 贡献占比 | 分解结果中某行对 scope delta 的带符号占比 |
| candidate score | 候选分数 | 检测排序分数，不表示概率 |
| flag level | 候选级别 | 候选优先级，不等于证明强度 |
| confidence grade | 置信等级 | assessment 使用的离散置信度档位，而非连续分数 |
| confidence rationale | 置信依据 | 对置信等级形成原因的结构化说明 |
| evidence-first lattice | 证据优先状态格 | `supported / contradicted / mixed / insufficient` 这类以证据状态为中心的判断格 |
| versioned snapshot | 版本化快照 | assessment/proposal 等对象按版本保留的不可变快照 |
| immutable snapshot | 不可变快照 | 一旦生成即不原地修改的 snapshot |
| latest assessment | 最新评估 | 某 proposition 当前生效的 assessment snapshot |
| blocking gap | 阻塞性缺口 | 当前阻止 assessment 升级或收敛的 gap |
| non-blocking gap | 非阻塞性缺口 | 已知存在但暂不阻塞当前结论形成的 gap |
| explicit rule process | 显式规则过程 | 可被 agent 审计的结构化推断流程，不依赖自由文本解释 |
| rule family | 规则族 | 一组共享判断目标或 assessment family 的 inference rules |
| rule registry | 规则注册表 | 对 `rule_id -> rule_family -> assessment_type` 做稳定解引用的规范元数据契约 |
| rule cluster | 规则簇 | 比 `rule family` 更细的业务分类，仅作规则元数据分组，不是稳定执行阶段 |
| rule version | 规则版本 | 单条规则定义的版本边界，用于 replay 与兼容检查 |
| registry version | 注册表版本 | rule registry 整体元数据的版本边界 |
| judgment policy | 判断策略 | 定义不同 assessment type 下 support / oppose / mixed / insufficient 口径的规范策略 |
| judgment threshold | 判断门槛 | judgment policy 中用于判断“证据是否实质成立”的稳定门槛 |
| candidate assessment | 候选评估快照 | 单次 recompute 中尚未提交、但已完成 canonical materialization 的 assessment 候选载荷 |
| candidate assessment identity | 候选评估标识 | 为打破 assessment / inference record 循环依赖而预分配的 candidate snapshot 标识 |
| materialization | 实体化 | 将 candidate judgment output 落成 canonical object 的过程 |
| cross-proposition inference | 跨命题推断 | 直接把其他 proposition 或其 assessment 纳入推断输入的能力；v1 inference engine 明确不纳入 |
| recommended next actions | 推荐下一步动作 | session 级 action proposal shortcut 的消费视图名称 |

## 文档写作约定

- `canonical` 正文优先译为“规范”，如 `canonical object`、`canonical state`；不要在同一语境中与“标准”“规范化对象”混写。
- `identity boundary` 固定译为“标识边界”；`identity normalization` 固定译为“标识归一化”，不要简写成泛化的“去重规则”。
- `lineage` 固定译为“谱系”；用于强调对象的来源和派生路径，不与 `provenance` 的“溯源信息”混写。
- `session-local` 固定译为“会话内局部”；用于强调 ID 或状态只在单个 session 内稳定。
- 正文中的动作语义优先使用中文，不要把英文动词直接夹在中文句子里；例如字段名保留 `` `supersedes_assessment_id` ``，但正文应写“替代旧评估”或“被新评估取代”，不要写“supersede 旧评估”。
- `seed` 在 evidence 设计语境下优先译为“种子”，如 `seed finding`、`system-seeded`；避免与“初始化输入”“前置证据”混写。
- `live evidence membership` 固定译为“实时证据归属”；用于 assessment 当前纳入的支持/反驳证据集合，不与 proposition 的 `seed_finding_refs` 混写。
- `snapshot` 在 assessment/proposal 设计中固定译为“快照”；若强调不可变版本语义，可写“版本化快照”或“不可变快照”。
- `anchor` 在 proposition/assessment 设计中优先译为“锚点”；`assessment anchor` 固定译为“评估锚点”。
- `intent` 在设计总纲里优先译为“意图”，在具体 Schema 文档里可根据上下文写作“步骤”。
- `artifact` 与 `projection` 必须固定译为“工件”和“投影”，不要混写成“结果详情 / 摘要视图”等其他核心术语。
- `analysis state surface` 固定译为“分析状态面”；`context surface` 固定译为“上下文面”，不要混写为泛化的“状态接口 / 上下文接口”。
- `SessionStateView` 与 `PropositionContextView` 正文可分别写作“会话状态视图（`SessionStateView`）”和“命题上下文视图（`PropositionContextView`）”。
- `live proposition` 固定译为“活跃命题”；用于表示仍在当前 session judgment track 中的 proposition，不要混写为“激活命题”或“在线命题”。
- `backing_findings` 固定写作“支撑事实单元（`backing_findings`）”；`focus_subjects` 固定写作“焦点主语（`focus_subjects`）”。
- `lookup handle` 固定译为“查找句柄”；用于强调对象只负责定位，不重复承载完整 payload 或 provenance。
- `explicit rule process` 固定译为“显式规则过程”；`rule family` 固定译为“规则族”。
- `rule registry` 固定译为“规则注册表”；`rule cluster` 固定译为“规则簇”，不要与 `rule family` 混写。
- `judgment policy` 固定译为“判断策略”；`judgment threshold` 固定译为“判断门槛”，前者是整套口径，后者是其中具体门槛。
- `candidate assessment` 固定译为“候选评估快照”；`candidate assessment identity` 固定译为“候选评估标识”。
- `materialization` 在 canonical object 语境下固定译为“实体化”；不要与泛化的“生成”“写入”混写。
- `cross-proposition inference` 固定译为“跨命题推断”；若指 v1 非目标，应明确写作“v1 不纳入跨命题推断”。
- `recommended next actions` 固定译为“推荐下一步动作”；若指字段名，保留 `` `recommended_next_actions` ``。
- `finding` 固定译为“事实单元”；不要与 observation、claim、evidence 混写。
- `proposition` 固定译为“命题”；用于表达“要判断什么”，不要混写为“结论”。
- `assessment` 固定译为“评估状态”；用于表达系统当前判断到什么程度，不要与 proposition 混写。
- `action proposal` 固定译为“动作候选”；若讨论旧设计中的 recommendation，可在首次出现时写“recommendation（动作候选）”。
- `priority axes` 固定译为“优先级轴”；用于 action proposal 的多轴排序语义，不要混写成单一优先级分数。
- `policy profile` 固定译为“策略配置”；用于显式描述 proposal 的策略上下文，不要省略成隐式运行时参数。
- `delta` 正文通常保留英文写法；若强调数学含义，可写作“变化量（delta）”。
- `scope` 在 `time_scope` 语境下译为“范围”，在 `filters / metric scope` 语境下可译为“作用域”或“观测总体范围”，但尽量避免与 `time_scope` 混淆。
- `candidate` 在 `detect` 中固定译为“候选”，避免写成“异常事实”或“异常结论”。
