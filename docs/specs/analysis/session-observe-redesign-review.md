# Session 与 observe 重设计：独立审查记录

Status: historical review complete; subsequent revision not re-reviewed，2026-10-20。
两轮静态审查结论只对应下述 R2 冻结稿；之后的用户反馈修订单列于文末。

审查对象：[Session 与 observe 重设计](session-observe-redesign.md)。
初始工作树基线：`622a930b7adba834744ad285789782e1d4d6e6b2`；
初稿 SHA-256：`6ec7e6144414edfb7a9dbb7ad31ee8e1b30e68d065aaba38a74e224d32a83415`。
用户授权独立 agent review 和设计修订；不授权代码、测试、打包技能或提交。

| 阶段 | 负责人 | 依赖 | 完成条件 | 状态 |
| --- | --- | --- | --- | --- |
| R1-A | api_review | 初稿 | 独立审查全部 API 的必要性、参数权威、生命周期和可表达性 | 完成：1 项补充 P2 |
| R1-S | semantics_review | 初稿 | 独立审查标量、空域、窗口、分配、计数和结果对齐 | 完成：1 项补充 P2 |
| F1 | coordinator | R1-A、R1-S | 保留原始发现，核验反例，修订合同和验收项 | 完成 |
| R2 | 原独立审查者 | F1 | 复核发现关闭情况及修订引入的相邻矛盾 | 完成：A01/S01 closed，无剩余 P2+ |
| V | coordinator | R2 | 文档路径、语法、差异和范围核验；明确未关闭事项 | 完成 |

最多两轮正式审查；未关闭的 P2 及以上发现阻止宣称设计审查完成。
审查证据只证明设计静态一致性，不证明目标 API 已实现或运行验收通过。
原有 attribute 计划及 public-surface 进度文档不属于本轮编辑范围。

## 第一轮原始发现

两位 agent 独立读取相同初稿、拥有者合同及相关代码，未互读结论，未编辑文件。
初次结果均为 No findings；协调者在第一轮内提出组合边界疑点，原审查者继续核验，
最终分别确认下列 P2。补充核验属于同一初稿的第一轮，不另计新轮次。

### R1-A：API 必要性及可表达性

原始首报结论：

> No findings：本轮在负责的 API 经济性、Session 生命周期和结果组合范围内，没有确认的 P1/P2 缺陷。

核对了原 6 个模块入口、15 个方法和 11 个属性。未将 values 不接受时间轴、
reconcile_run 的显式归属或已披露的标量结构变更当作缺陷。
证据范围：`session/__init__.py:71–288`、`session/core.py:99–629`、
`session-state-and-runtime.md:7–24,67–111`、`python-analysis-design.md:34–47,108–123`。

补充核验保留结论：单时间域已有公开构造路线，**不构成缺口**。
`unit = mv.coordinates(by=(), keys=((),))`，
`P = mv.product(calendar, unit)`，再用 `session.frame(P)`。
现行证据：`dsl/population.py:624–650,660–690`、
`tests/analysis/temporal/test_analysis_temporal.py:446,618`。
审查者完成了当前 CalendarAxis 路线的捕获 probe，包括 project/where 构造；
未执行未来 interval API。因而本轮不放宽 product 的最少因子数，也不新增 constructor。

**A01 / P2：非平凡日历 Population 子集没有公开生产路线。**

原始补充发现：

> 提案 :238–239 和 O13 :559 要求“Population 中的选定日历子集”保留原完整目录，但未定义如何通过公开 API 产生该子集。例如 Jan1–Jan4 完整目录只选择 Jan2/Jan4，保留原位置 1/3。

- CalendarAxis 只有 scope/grid/kind/show，无 selector：`_temporal.py:3453–3499`。
- coordinates 拒绝 calendar；Population.where 接 PopulationPredicate，sets 要求相同完整 calendar binding：`dsl/population.py:272–289,364–390,624–650`。
- Frame 虽可筛选，population_from 不接 calendar：初稿 :424、`dsl/frame_population.py:142–147`。

影响：O13 无法按所述公开入口完成，实施者需要自行增加未设计的选择 API，
或将一个改变目录的更小 CalendarAxis 误当原目录子集。
原建议允许两条修复方向：明确延期该来源选择能力，或定义保留目录的公开生产路径。
本轮选择前者，不扩大 API 范围。

### R1-S：核心语义与组合闭合

原始首报结论：

> R1：No findings。在分配范围内未发现可构成 P1/P2 的数值错误或双重合法解释。

检查了零轴/单 interval、空域/presence、窗口端点和历史、硬条件、分配分母、
显式计数域、对齐和冷恢复。首报认可这些已明确的核心决策；未将未来 API 尚未实现列为缺陷。

**S01 / P2：interval 的声明目录在 outer 与筛选后缺少闭合定义。**

原始补充发现及位置：初稿 :330–340，关联 :192、:352–354。

> A 声明 [Jan 1, Feb 1)，B 声明 [Jan 15, Feb 15)。A.align(B, how="outer") 合法产生两个不同 interval 坐标和共享轴。再 where/top 只留第一行，与单 interval C 做 ordinal。“一个声明区间”可被实现为当前剩余一行，或原完整声明域，两种实现分别接受、拒绝；冷恢复也无法单凭剩余行判断。

拥有者证据：`query-result-reuse.md:454–482` 区分声明目录和实际行；
`core/frame_expression.py:326–336`、`methods/frame_values.py:42–59` 的 where/top
保留 schema 并筛选实际行。新 interval 的目录生命周期不能只靠 calendar 专属规则推断。

原建议：保存有限冻结 interval 目录，outer 合并/去重、允许区间重叠；
where/top/select 保留完整声明目录；ordinal 仅对两边各一条声明记录准入；
目录随持久化/冷恢复保持。增加一个组合验收，无需新 API 或多区间 ordinal。

## 修订与复核

### F1 修订

| 发现/补充 | 修订位置 | 修订结果 | 独立复核 |
| --- | --- | --- | --- |
| A01 | §3.2、§6.1、§11、O13 | 限定完整 CalendarAxis 来源域；非平凡日历 Population 子集延期；Frame 筛选保留目录，不能把日历轴桥接回 observe | closed |
| S01 | §5.1、§8.1–8.2、O29 | 定义有限 interval 目录及等值合并、重叠、筛选/规约/恢复传播；ordinal 看声明记录数，不看实际行数 | closed |
| 单时间域构造说明 | §3.2、§9.2、§10、O13 | 写出已有零轴单元域乘积路线；product 仍至少两个因子，不新增 API | closed |
| 空域组合说明 | §8.1 | 明确空/单元零轴域的 exact/outer 与 presence，保持已有实际键域原则 | closed |

R2 仅复核修订、原始发现关闭和相邻回归；不安排第三轮。

### R2 冻结稿

受审主稿 SHA-256：`36b2ef502d8ff6303e5a13311b8777528e68b067163d07dc07309a22e202d3dc`。
两位审查者均在开始与结束时确认相同受审 hash；当时交付稿与之相同。
此后主稿的变化见“后续用户反馈修订”，不能沿用该 hash 的完整审查通过声明。

### R2 独立结论

api_review 原始结论：

> R2 结论：A01 closed；本负责范围无剩余 P2+，无新增 findings。

复核位置：主稿 :126–131、:250–253、:569–570、O13 :596；
单时间域路线 :109–111、:470–471、:531–534；interval/空域 :357–369、:380–384。
确认输入能力收窄不改变合法 P 的指定域求值，未新增 selector、广播或来源恢复；
确认其 R1 首报及补充记录准确。

semantics_review 原始结论：

> R2：S01 closed；未发现修订引入的 P2+ 问题。

复核位置：主稿 :204–205、:357–369、:380–384 和 O29 :612；
日历域收窄 :126–131、:250–253 和 O13。
确认目录/实际行分离、重叠 interval 合并不合并贡献、筛选不改变 ordinal 准入、
持久化保留目录，以及窗口和来源桥接边界一致；确认其 R1 记录准确。

### 最终核验与限制

静态检查通过：主稿、审查记录和索引共 27 个本地链接，4 个 Python 代码块语法，
代码围栏配对、O01–O29 连续编号及 tracked/untracked 文档空白检查。
最终核验脚本曾把 `git diff --no-index --check` 的正常差异退出码 1 误判为失败，
原始输出无空白诊断；修正脚本对 0/1 退出码的解释后重跑通过，未改动受审主稿。
R2 当时交付主稿与受审 hash 相同，未关闭 P2+ 为 0。
本轮编辑范围为主稿与本文；索引链接属于前一轮已有文档改动。
原有未跟踪 attribute 实施计划和 public-surface 进度文件保持在本轮范围外。
未修改代码、测试、AGENTS.md 或打包技能，未提交。
审查仅证明本提案的静态一致性；O01–O29 均仍是未来实施验收，
没有目标 API、Runtime、数值或冷恢复通过证据，也没有第三轮审查。

## 后续用户反馈修订

2026-10-20，用户要求 timezone 默认使用系统时区，并追问 member_count 的使用方及
是否有必要使用独立名称。本次仍只修订设计文档，没有新增独立 agent 审查轮次。

| 项目 | 依据及修订 | 状态 |
| --- | --- | --- |
| U01：时区默认 | create.timezone 改为 str 或 None；省略时仅在创建时解析系统时区并保存名称/解析方式，显式参数优先，恢复时不重新探测；同步 O22 | 文档完成 |
| U02：计数必要性 | 核对现有调用者和 Entity/Field.count；保留显式身份域计数语义，进一步建议 Population.count()，删除独立 member_count 导出；同步签名、示例、迁移和 O01/O15 | 文档建议完成，未实施 |

U01 沿用当前系统时区解析事实：`analysis/timezone.py:43–73`、
`_timezones.py:29–73`；现行用例在
`tests/analysis/temporal/test_analysis_session_timezone.py:22–59,89–133`。
本次只读取这些代码和测试，未重新执行运行验收。

U02 的计数合同依据：`semantic/expressions.py:248–254`、
`docs/specs/semantic/expression-model-redesign.md:155–167`。
成员计数实际调用主要位于 7 个测试/类型验收 Python 文件与 DSL 示例；
`test_query_results.py:428–480` 明确验证按 P 筛选后的来源贡献 2 个、声明身份 3 个、收入 30、
名单分母 3 及每人收入 10，证明不能直接换成来源行 count。
统一 P.count() 属于入口经济性建议，不改变原审查过的显式域计数、分组和窗口语义。

上述后续修订未取得新的独立复核结论；原 A01/S01 修订仍保留，
不将旧 R2 的 No findings 外推到新默认值或新的方法拼写。
后续修订主稿 SHA-256：`8281250ae5ec3e21b86b890efa18a214ce57a82d2115b633adfcea7830d95d8d`。
文档静态核验通过：27 个本地链接、4 个 Python 代码块语法、围栏及 O01–O29 编号；
已检查无旧 UTC 默认签名或独立 member_count 目标签名残留，差异无空白错误。
