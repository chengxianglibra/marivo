# Period Comparison：分阶段实施计划

Status: implemented，2026-10-21。S0–S5 已完成开发、阶段审查与本地统一验收；
实际证据及 Runtime、冷恢复和发布验收的范围记录在本文。

语义以 [Period Comparison 设计](period-comparison-design.md) 为准，验收沿用其 PC01–PC16，
不另建一套语义编号。当前合同仍由 [AnalysisFrame](query-result-reuse.md)、
[Python Analysis Design](python-analysis-design.md)、
[Timezones and Calendars](timezone-and-calendar-design.md) 和
[Operators and Frames](operators-and-frames.md) 拥有。

实施授权：agent 团队执行 S0–S5，每任务先计划再开发，修复所有 P2+，
同一任务最多两轮正式审查，完成统一验收后创建本地 commit，不 push。
不修改 AGENTS.md 或 packaged skills。

## 1. 交付范围与实施基线

交付一条完整路线：

```text
mv.offset(years=... | periods=...)
    -> periods.shift(rule)
    -> session.observe(metrics, by=...)
    -> current.compare({"yoy": yoy, "mom": mom}, ...)
    -> ordinary AnalysisFrame / compute / persist / saved-data operations
```

必须一起落地的行为：

- 周同比按 ISO week-year 的相同周号；缺少 W53 时没有对应，不退到前 52 周。
- 完整桶对应完整桶；周/月/年前缀按民用日历位置取得同进度。不裁短当前值，不夹取不存在的日期。
- shift 保留精确稀疏或空选择、冻结直接映射；空域为零端点，链式 shift 只知道直接前驱。
- 每个端点独立求原 Metric。窗口距离来自名义日历；输出稀疏性不压缩窗口，也不重选贡献 Population。
- 单、多基期均使用命名 compare，保留本期轴和一份当前值；每个基期产生 baseline/absolute/relative。
- exact 检查实际完整联合键域；outer 保留可映到本期域的联合键并披露 presence，拒绝域外实际基期时间行。
- 完成结果保存有限类型化说明；冷恢复和后续值运算不回源、不恢复 Metric。

不扩展业务日历、任意中段进度、通用映射语言、自动基期查询或跨源快照协议。
复用现有 compiler、scheduler、Ibis/pandas 数值路径、Arrow 和发布事务，不增加执行器或恢复状态机。

### 当前落点核验

计划起草基线：2026-10-20，HEAD `a56cb534eee3`；实际实施基线见下方协调记录。
以下为起草时的代码阅读事实，未运行实现验收：

| 当前结构 | 本次必须处理的影响 |
| --- | --- |
| `BoundTimeGrid` 假设非空连续分区；冻结目录把选定位置作为连续 ordinal | 分开名义日历位置与精确选定域，才能正确表达稀疏、空基期和窗口距离 |
| 空日历在 observation binding 中可退成 `period=None`，窗口端点有 scalar fallback | 显式区分“无时间轴”与“有时间轴但零端点” |
| 来源预过滤用包围区间，分桶 CASE 的间隙可落到 null | 未选择区间不能制造输出分类、null 桶或无关数值错误；窗口实际需要的贡献仍须读取 |
| compare 和 align 共用二输入 `_combine` / `FrameCombineProgram`，时间 match 生成 paired 轴 | compare 单独拥有 N+1 输入和本期轴整形；align 保留双端语义 |
| 附件按输出 schema 的引用收集，select/reduce 会重建 schema | 直接映射与比较列说明必须有类型化 owner，并同步列裁剪、重命名和降维 |
| 起草时 Store 13 / v2；实施前 Attribute 已升至 Store 14 / v3 | 本次使用下一代 Store 15 / v4 一次清切换，不能把新说明塞入不受校验的 audit 或 pandas attrs |

工作区已有 README 修改及 attribute、predicate、observe 等其他设计文件。
这些内容不是本计划的依赖或已实现证据；实施不得顺带落实其他提案。

## 2. 阶段、依赖与职责

S0 的数据结构稳定后，S2 与 S3 可在明确文件 owner 的前提下并行；S4 等待二者集成。
阶段是内部工作边界，不是独立发布点。最终只发布一套新合同。

```text
S0 类型与边界
  -> S1 offset / shift / 冻结日期对应
       -> S2 显式 observe / 窗口 / 来源执行 ─┐
       -> S3 命名 compare / 当前轴结果 ────┤
                                           -> S4 后续组合 / 保存 / 冷恢复
                                                -> S5 公开切换 / 清理 / 统一验收
```

| 阶段 | 责任角色 | 依赖 | 退出标准 | 状态 / 证据 |
| --- | --- | --- | --- | --- |
| S0 | develop_temporal、develop_frames / review_s0 | 设计与当前消费者核验 | 精确域、直接关系、列说明、附件引用和版本目标闭合 | accepted / R2 No findings，94 tests，审查 2/2 |
| S1 | develop_temporal / review_s1 | S0 | offset 与 shift 的日期/目录行为成立，已有 align 规则仍可用 | accepted / R1 No findings，审查 1/2 |
| S2 | develop_sources / review_s2 | S1 | 稀疏/空时间域贯穿 observe，原 Metric 与窗口数值正确 | accepted / R1 No findings，审查 1/2 |
| S3 | develop_frames + consumers / review_s3 | S1 | 单、多基期共用 N+1 路径，当前轴、联合键和严格检查正确 | accepted / R2 No findings，两项 P2 关闭，审查 2/2 |
| S4 | develop_frames（组合）/ develop_storage（保存）；coordinator 协调 | S2、S3 | 说明随运算正确传播，保存与冷恢复闭合 | accepted / R2 两份 No findings，两项 P2 关闭，审查 2/2 |
| S5 | develop_disclosure；coordinator；独立审查/验收者 | S4（最终验收依赖） | 旧入口清理、公开披露同步、PC01–PC16 与最终检查通过 | accepted / R2 两份 No findings，两项 P2 关闭，最终 gate 5199 passed，审查 2/2 |

实际开始时给角色绑定具体执行者；本计划不预建多份进度文件。
在本表记录完成日期、变更范围、检查结果及失败定位；PC 对应证据在 §5 补充。
共享的 `data_schema.py`、`frozen_calendar.py`、`graph_plan.py` 由集成负责人协调，
同一时段每个文件只有一个修改 owner，其他工作包提交所需接口与消费要求。

### 实施协调记录（2026-10-20）

- 实际开始 HEAD：`fe674d76dc6684021d972e33dbd14fb529579722`。
  原计划的 `a56cb534eee3` 之后已合入 Attribute；当前 owning source 为 Store 14、
  `marivo.frame_output/v3` / `frame-output-v3`。本次目标冻结为 Store 15、
  `marivo.frame_output/v4` / `frame-output-v4`，在 S4 一次启用。
- 已有工作区：analysis README 修改；attribute local draft、period comparison 设计/计划、
  predicate、observe 设计/审查、public surface progress 等未跟踪文件。
  保留所有无关内容；README 只暂存本任务相关 hunk。
- 探索：`explore_temporal`、`explore_frames`，`gpt-6-luna` / `max`。
  开发：`gpt-6.1-sol` / `high`；独立审查：`gpt-6-astra` / `high`。
  coordinator 维护本文、文件 owner、依赖、验收和最终 Git 范围。
- 每个开发任务先写 `evidence/period-comparison/<task>-development-plan.md`，
  记录具体文件、合同、取舍与验证；原始失败、审查及修复保留在同目录。
  evidence 为本地运行记录，本文保留可跟踪结论。
- 正式 R1 检查候选；开发修复后 R2 定点确认及相关回归。
  不开启第三轮；R2 后剩余 P2+ 阻断验收和 commit。
- S0/S1 暂定 owner `develop_temporal`；先等待探索闭合精确类型和接口再编辑。
  S2/S3 待接口冻结后分派，避免共享文件并发写入。
- S0 开发前计划：`evidence/period-comparison/s0-s1-development-plan.md`。
  已采纳探索结论：保留现有 `ordinal` 的选定顺序语义，增加独立 `nominal_ordinal`
  表达名义日历距离；空/稀疏选择不重新压缩名义距离，`ordinal()` 继续完整桶准入。
  `develop_temporal` 当前独占 `_temporal_grid.py`、`_definition/types.py` 和
  `core/data_schema.py`、`frame_structure.py`、`frame_calendar.py`、`frozen_calendar.py`；
  compare 列组类型在该 owner 写入冻结后闭合。S1 规则与日期 kernel 暂未开始。
- 时间探索已完成：`evidence/period-comparison/s0-s2-temporal-exploration.md`；
  已确认来源包围范围/null 分桶、空窗口 scalar fallback 及 Attribute 共享消费者。
  探索未运行测试，不作为实现证据。
- Frame 开发 owner `develop_frames` 已分派 S0 命名列组、S3 和 S4；
  先计划并等待 shared-file handoff，不与时间 owner 并发修改共享 schema。
- S0 时间结构首个定向检查：`s0-structure-tests.log`，77 passed；
  这只证明当前结构测试，尚未完成 S0 集成/审查或来源、保存验收。
- S0 时间子范围已冻结交接：`evidence/period-comparison/S0-interface.md`。
  完整定向结构测试 87 passed；7 个实际修改模块的 typing、lint/import contracts 通过。
  首次 typing 失败及修复保留 `s0-development-failures.md`。
  `develop_frames` 现独占共享 schema/structure/frozen-calendar 和结构测试，
  补齐比较列绑定及附件闭合后提交整个 S0 审查，当前仍为 0/2。
- S2 owner `develop_sources`（`gpt-6.1-sol` / `high`）已开始开发前计划；
  来源代码等待 S0/S1 验收，不与其他 owner 并发改动 graph_plan。
- S0 命名事实计划/冻结：`s0-frame-facts-development-plan.md`、`s0-frame-facts-freeze.md`。
  比较说明采用 role/source-path/output-path 的精确绑定、闭合普通/calendar/interval 变体；
  required 附件和消费映射通过既有结构验证，不保存可执行状态。
  组合检查：91 passed（12.72s）、7-source typing、定向 lint/import contracts 全绿。
- S0 R1 候选：`694b1855903766ade2d41ea7bba129ade7611f471a4d5646f56af67783e6ff02`，
  9 个 source/test 文件摘要见 `evidence/period-comparison/s0-candidate.json`。
  `review_s0`（`gpt-6-astra` / `high`）开始第 1 轮正式审查；产品写入冻结。
- S0 R1 已核验的原始问题（正式报告待完成）：Berlin `2026-10-25`、
  `grain('hour', count=2)` 的 13 个合法桶被新名义序号计算判为重复；
  原基线归档接受同一目录。反例/基线日志为 `s0-review-round1-fold-probe.log`、
  `s0-review-round1-fold-base.log`。S0 未验收，修复等待完整 R1 发现表。
- S0 正式 R1 完成：`evidence/period-comparison/s0-review-round1.md`，2 项 P2。
  S0-R1-01 为上述 fold 序号回归；S0-R1-02 为 comparison facts 仅核对 key dtype，
  允许不兼容的 day→month/时区等解释通过完整数据准入。
  原始反例均保留；审查独立运行 91 tests 通过（4.06s），候选 9 个摘要未变。
  统一修复 owner `develop_frames`，含 `_temporal_grid.py`；先记录修复计划后编辑。
  只剩第 2 轮审查，未解决 P2+ 阻断 S0 及后续阶段。
- S0 修复计划/结果：`s0-repair-plan.md`、`s0-repair-result.md`。
  子日名义序号使用精确 UTC 微秒；calendar facts 复用既有兼容校验，interval 独立。
  修复候选 `da02466010ff35901f9b220e8976cee731d630bf2bd783f3c52b1f47baae827f`
  （`s0-repair-candidate.json`），94 passed（4.36s）、typing/lint/import contracts 通过。
  原 fold 反例接受 13 桶，原坏比较说明在 schema 构造时拒绝。
  `review_s0` 已开始 R2，源/test 再次冻结；不增加第三轮。
- S0 R2 完成并验收：`s0-review-round2.md`，**No findings**，两项原始 P2 关闭。
  独立 94 passed（4.15s），原始两探针分别接受/拒绝符合修复；
  9 个候选摘要前后一致，R1 报告未改。
  子日 `nominal_ordinal` 是 UTC 微秒排序身份，不作为期间数量距离；当前没有该算术消费者。
  S0 只验收结构，不代表来源、日期对应、保存或最终门禁。
- S1 已释放开发：`develop_temporal` 先完成 `s1-development-plan.md`，
  拥有时间/规则及 frozen date matcher、必要的早期 export/registry 接线。
  从初始时间域构造捕获民用边界，fixed-data 匹配不查当前 TZ；
  S5 仍负责完整公开披露和导航。S2/S3 等待 S1 验收。
- S1 具体开发前计划已保存并开始实施；新增民用边界在初始构造捕获，
  shifted 域不伪造包围 TimeScope（普通域保留 scope，shifted scope 为 None）。
  数据侧 intent 区分合法无对应和目标未选中，用于 S3 检查显式规则与直接关系冲突。
- S3 开发前计划已完成：`evidence/period-comparison/s3-development-plan.md`。
  `graph_plan.py` 明确串行 owner：S2 来源修改先冻结交接，再由 S3 接入 N+1 program；
  其余 S3 文件在 S1 验收后独立开发。
- Runtime 环境初始只读检查：PostgreSQL 专用 Docker socket 当前不存在
  （`runtime-initial-status.log`）；尚未启动服务或执行 Runtime，S2 需要时再准备。
- S1 开发中检查：首次 11-source typing 通过；结构首跑 132 passed / 3 failed，
  更新稀疏/前缀合同的旧断言后核心 155 passed（5.14s）。
  匹配首跑 29 passed / 22 failed，已分类为旧 calendar fixture 字段、
  已移除 live TZ lookup 的 monkeypatch owner 及准入措辞；迁移该 fixture 后重跑中。
  原始日志 `s1-structure-initial.log`、`s1-core-tests*.log`、`s1-matching-tests-initial.log` 保留。
  没有为这些失败提前切换 Store 或修改 S2/S3 运行时。
- S1 最终候选冻结：`s1-candidate.json`，
  `27a0358ae9f06272ecb324a7417592ce22a5059d25fa1856081418ca7edc3bf3`；
  24 个实际 S1 source/test 文件含未跟踪 shift 测试，清单和 `s1-source-freeze.tar` 摘要一致。
  最终 258 passed / 78 既有 pandas warnings（11.08s），12-source typing、
  24-file lint/import contracts、diff 检查通过。
  接口/结果和全部失败分类：`s1-interface.md`、`s1-development-result.md`。
- S1 最终 lint 曾暴露 neutral `_temporal` 反向导入 Analysis 错误模块；
  未弱化 import contracts，改为沿用既有 TimeScope/CalendarAxis 构造 ValueError 边界，
  保留 expected/received/repair 和 cause。Analysis offset/frozen-match 仍为 AnalysisError。
  此取舍提交独立审查；`review_s1`（Astra high）R1 已开始，产品/test 写入冻结。
- S1 R1 完成并验收：`s1-review-round1.md`，**No findings，无未解决 P2+**。
  独立 114 passed（5.56s）；正向闰期/跨年 ISO 前缀、空映射不同意图、
  零偏移 fold 区分、年份越界探针通过；24 个当前文件和归档摘要一致。
  聚合摘要使用 compact JSON，精确算法见 `s1-candidate-hash-algorithm.md`；
  审核脚本两次设置错误保留原始日志，不归为产品失败。
- S2/S3 已释放并行开发；各自刷新已有开发前计划后编辑。
  S2 独占 `graph_plan.py` 来源侧，冻结后明确交接 S3；S3 其余文件独立开发。
  S2 另拥有 `numeric/test_remote_numeric_query.py` 的单个 PostgreSQL witness，
  待测试准备后由 coordinator 准备环境；其余 numeric/statistics compare 消费者归 S3。
- S2 首个共享 hunk：`frame_evaluation.py` 的 source 结果附件收集须对 Calendar 使用
  `FrozenSourceCalendar.attachments`，Interval 保留 `.directory`。
  该文件仍由 S3 owner `develop_frames` 独占并立即代入该 hunk，S2 不并发编辑。
- S4 保存 owner `develop_storage`（Sol high）先只做开发前计划，
  后续独占 terminal protocol/store/read/preview 与 cold worker；
  Frame owner 继续拥有组合传播。两者等待 S2/S3 验收后实施并统一 S4 审查。
- S2/S3 审查冻结协调：共享 `frame_evaluation.py` / `graph_plan.py` 的候选先集成、
  **一起冻结后**分别做 S2 与 S3 独立审查，避免审查期间 whole-file 摘要移动；
  各任务仍各自最多 2 轮。S2 确认 `graph_plan.py` 无需修改，已正式交接 S3。
- S2 首轮 source 检查 36 passed / 3 failed：一项新测试设置已修复，
  两项为 S3 改动中的临时 `compare_frame_data` 导入阻塞；原始日志保留，待集成后重跑。
- PostgreSQL 专项测试已准备：
  `test_remote_numeric_query.py::test_postgres_sparse_calendar_keeps_window_contributions`。
  coordinator 已按仓库流程启动专用 VM 和 PostgreSQL profile，环境日志
  `runtime-vm-start.log` / `runtime-pg-start.log`；PG 17.11、只读 reader 权限检查通过，
  这仅为环境证据，尚未执行数据验收。原来停止的 VM/profile 在本任务结束时停止，不删卷。
- S4 保存开发前计划已完成：`s4-storage-development-plan.md`。
  追加的 activation owner 为 `core/artifact_data.py` 的 `frame.v4`、
  evidence digest、telemetry v15 路径和 reconciliation 当前说明；不机械升级未变 nested tags。
  S4 运行 focused/cold 检查，最终 broad default 由 S5 公开消费者切换后的未收窄 gate 覆盖。
- S2 开发期 PostgreSQL witness：`s2-postgres-runtime.log`，1 passed / 0.66s，无 skip。
  这是相关 SQL 路径证据，其他 provider 不重跑；最终相关源代码变化后重验。
- coordinator 集成核验发现逐 cell OR 会影响普通连续域，S2 实测 730 天来源观察
  RecursionError（36.29s，`s2-long-calendar-initial.log`）。
  微探针：逐 cell SQL 125541 bytes / 0.3205s，单 range 164 bytes / 0.001s
  （`s2-interval-predicate-probe.log`）。已采用相邻 actual interval 合并的最小修复，
  不改变稀疏间隙/窗口合同、不新增框架或缓存；新增单个真实来源长连续域回归。
  S2 同时强化 distinct/median 原贡献 oracle，使其明确区别于逐桶结果再聚合。
- 730 天对照最终归因：HEAD `prepare_fields` 基线也触发既有 nested bucket CASE
  RecursionError（63.00s，`s2-long-calendar-baseline.log`）；coalesced 当前为 30.24s。
  保留完整探针/测试快照，不修改未变 compiler CASE，也不在产品套件保留新引入的失败测试。
  引入的 OR SQL 增长修复已保留；不宣称 730 天来源旅程通过。
- S2 source component 已冻结：`s2-source-result.md`、6-file manifest/snapshot/patch；
  最终本地 39 passed（14.65s）、4-source typing、6-file lint/import contracts 通过。
  coalescing 后 PostgreSQL 重跑 `s2-postgres-runtime-coalesced.log`：1 passed / 0.65s，无 skip。
  旧 compare caller 导致的 broader run 为 67 passed / 1 failed，明确非绿；归 S3 迁移。
- S3 核心专项 119 passed（7.98s）；首次 broader 行为跑 748 passed / 23 failed，
  owner 正在区分现行调用迁移与 S4 codec，原始结果保留。
  为缩短串行尾部，`develop_sources` 另领 S3 consumer 子包（先计划），
  独占 18 个 numeric/statistics/graph 测试；S2 六文件仍冻结。
  `develop_frames` 保留所有产品代码、comparison fixture、frame-plan/method-facts 和核心 materialization 测试。
  S3 generic 新 presence 保存/传播校验作为显式 S4 交接，不通过路径前缀猜测绑定。
- S3 第三工作包 `develop_storage` 先计划后迁移 10 个 materialization 当前消费者；
  与 Sources 的 18-file 包、Frame 的核心包互斥。配对 producer 按真实目的判断 align，
  不机械替换 Attribute/align 的合法布局；保存和恢复断言留待 S4，不删除或永久跳过。
  这两个消费者子包汇入同一个 S3 候选和最多两轮正式审查，不各自增加审查循环。
- S4 core owner 在等待消费者期间准备组合传播/新 presence 完整性计划，
  如需精确 endpoint-presence 绑定则先闭合类型和 codec 交接；S4 源码仍等待 S2/S3 验收。
- S3 core 已冻结：`s3-core-candidate.json`，
  `fc5ffeabaaf2ef82dd987c4a4a78e4b76d733849607299ab35cb8d99f5197136`，17 文件快照；
  最终 218 passed（11.28s）、10-source typing、17-file lint/import contracts 通过。
- 18-file consumer 包已冻结：`s3-consumers-result.md` 及 manifest/snapshot/patch。
  最终穷尽 local run 166 passed / 30 failed（42.55s），30 项均为
  `dataset.terminal_output` closed v3 codec，0 未分类/消费者失败；原始及 JUnit/机器分类保留。
  此运行非绿，S4 切换后须重跑这些原断言。18-file lint/import contracts 通过，S2 六文件摘要未变。
  pandas 旧 delta rename 与新 current `value` 重名的 recipe 改为显式 assign，保留原 score oracle。
- 10-file materialization 包已冻结：`s3-materialization-consumers-result.md`。
  本地 196 项通过；4 个原始 persist 节点保持待 S4，最终 nonblocked run 显式 deselect，
  没有新增 skip/xfail 或移除断言。10-file lint/import contracts、9-file optional typing 通过。
  Attribute 测试剩余 19 条 optional typing 诊断对照 exact HEAD 测试+当前产品，未引入新消息；
  不把此探针宣称为完整 archived-product typing 或该模块全绿。
- S2/S3 集成冻结完成：`s2-s3-candidate.json`、`s2-s3-source-freeze.tar`，
  `7babbf607562f4495bcf78f74bf9e4700c0cb14045cd43e1bb6eb8f4344d430d`，72 source/test 文件。
  四个组件的全部当前摘要仍一致，变更路径无 unowned 文件；基线 HEAD 未变。
  `review_s2`、`review_s3`（均 Astra high）在同一候选分别进行 R1，产品和测试整体冻结。
  coordinator full-product typing（310 source files）及 72-file lint/import contracts 通过；
  这不是 S5 最终 broad gate。
- S4 core 开发前计划 `s4-core-development-plan.md` 已协调：扩展现有 DataComparison，
  用闭合 EndpointPresence、命名空间及 enclosing records 保存精确 presence 关系；
  select 没有直接值绑定时保留 presence-only facts，不另建 schema-wide 推断图。
  同域延续保留原说明；显式时间操作替换原时间权威时，旧值恢复普通保存值，
  不错误重绑旧窗口/映射，仍保留精确的继承 presence。该选择对应设计 §8。
  core 字段先冻结再交接保存 codec；S2/S3 正式审查期间不修改产品或测试。
- S2 正式 R1 完成：`s2-review-round1.md`，No findings，无 P2+。
  独立 39 项回归与 6 项边界探针通过；72 文件与归档 entry/exit 摘要一致。
  审查验证 gap 严格检查、合法窗口贡献、all-history、显式稀疏 sample、空 Population
  和空 calendar count-only 语义。原始探针误用和既有常量分母 lowerer 问题保留并分类，
  未扩展本次范围；Runtime 沿用已执行的 PostgreSQL witness，不宣称其它 provider 或 release。
- S3 正式 R1：`s3-review-round1.md`，两项 P2，均阻断验收：
  R1-S3-01 显式 match 仍被基期不相关的 immediate origin 提前拒绝；
  R1-S3-02 两个 active Attribute paired-parent producer 遗留 binary compare。
  owner `develop_frames` 先记录修复计划，再按设计 §6.1 分开显式规则与直接关系推导准入，
  并把这两个 paired producer 迁移为 align，保留全部 Attribute 行为断言。
  原始审查独立 217 passed；完整 Attribute 30 passed / 3 failed，其中 2 项为该发现，
  1 项为已声明的 S4 codec；72 文件 entry/archive/exit 未变。修复冻结后仅剩 R2，
  不将未证明公共可达的 pending-nullability 内部实验当作额外缺陷扩展范围。
- S2 Runtime 窗口结束；本任务启动的 PostgreSQL 和专用 Colima VM 已停止，
  数据卷保留（`runtime-pg-stop.log`、`runtime-vm-stop.log`），恢复开始时停止状态。
- S3 修复先计划后开发，证据 `s3-r1-repair-plan.md` / `s3-r1-repair-result.md`。
  基期自身直接关系先校验；显式规则遇不相关 origin 时直接匹配两个冻结端点，
  省略规则仍拒绝且给出正确 repair。两个 paired Attribute producer 使用 align。
  最终专项加完整 Attribute：250 passed / 1 已登记 S4 codec failed；typing、lint 通过。
  R2 集成候选 `s3-r2-candidate.json`、`s3-r2-source-freeze.tar`，72 文件，
  `f0a7421b48429e344c7e01feb5e13e9a0606970d9d37c5a992a3901b3b6cb51d`；
  对 R1 仅 3 个修复文件变化，S2 六文件未变。最终 R2 只读进行，S4 仍未释放。
- S3 最终 R2 完成：`s3-review-round2.md`，No findings，两项 P2 均关闭。
  独立 250 passed / 1 相同 S4 codec failed；原始显式 origin 探针改为成功，
  对自身 direct 损坏、省略规则、等价 monthly、空 W53 冲突和 paired parent 的回归通过。
  72 文件 entry/archive/exit 摘要一致，S3 以 2/2 轮验收，不再开启第三轮。
- S4 已释放 core/storage 两个开发包，严格按已协调计划实施。
  Frames 先冻结闭合字段交接 Storage；Storage 可并行编辑不共享的版本与消费者，
  待 type freeze 后接 codec。共享源文件一次仅一个 owner；统一冻结后做 S4 正式审查。
- `explore_frames`（原 Luna max owner）在 S4 开发期间只读刷新 S5 当前消费者清单，
  专查已迁移测试之外的 active binary compare/旧路径遗漏；不重开已验收语义审查，
  不编辑产品、测试或 owning docs，也不把历史与 align/Attribute 合法形状误判为待清理。
- S4 core 闭合字段已冻结并交接 codec。实际传播发现静态 axis descriptor 不包含完整选定域，
  单靠 token/name 无法区分同域延续与时间权威替换。批准在既有 StageOutput 通道携带
  具体 DataSchema，由 effective_frame 消费并交给最终完成结果；不增加 executor/scheduler，
  不序列化 StageOutput 或恢复来源图。Frames 因此额外独占 graph_exchange、
  query_runtime_types、graph_local_execution、frame_evaluation 以及 graph_source_execution
  的 fixed-input schema 交接 hunk；S2 六文件不动。列/键形状仍受原边界校验，
  验收须覆盖 pending 链与完成/保存链的同域和替换时间域行为。
- S4 保存已接入 Store15/v4 的闭合记录和 finite-authority 预览路径，仍在开发检查，
  不以 constants 改动宣称保存验收。Luna 刷新发现额外 active recovery 消费者：
  test_attribution_recovery / attribution_recovery_worker、test_relation_recovery、
  test_versioned_ownership；批准 Storage 先补计划再做窄调用/输出路径迁移，
  保留冷恢复、receipt、无 source 和 ownership 拒绝断言。
  test_analysis_temporal 与 test_terminal_composition_boundary 的剩余 API 消费者归 S5，
  不把这次消费者闭合作为 S3 第三轮审查，也不机械改写 align/Attribute 布局。
- S4 冷旅程初始 global ZoneInfo tripwire 在 Session.resume 的既有 ReportTimeAuthority
  时区名称校验处触发，尚未访问保存 Frame；原始失败保留。
  批准来源/模型/日历重建 tripwire 仍在 resume 前启用，global ZoneInfo 在 resume 后、
  恢复任何 endpoint/report 之前禁用。证明边界为保存 Frame 读取、自动 compare 与后续运算
  不重算当前时区边界；不宣称 Session bootstrap 本身可在 ZoneInfo 不可用时工作，
  不扩展本次实现到 Session admission。
- S4 初步核心专项 197 passed，pending 链和嵌套损坏 witness 仍在补齐；不代表 S4 已验收。
  为减少串行尾部，在 S1–S3 已接受、S4 类型闭合后释放 S5 独占公开面的预备实施。
  S5 最终验收依赖 S4 不变，S4 正式冻结前所有 owner 暂停写入；API/site build 与 broad gate
  仍由 coordinator 排期。S5 先刷新开发计划，再处理其原 owner 列表及 Luna 补充的 temporal /
  terminal-composition 消费者、correspondence docstring、frame.select 示例。
  tests/surface/test_session_discovery_help.py 整体交 S5，Storage 不再并发修改该文件。
  _temporal.py 仅文档/披露部分从已验收 S1 交接 S5，不变更时间算法。
- 额外 attribution cold 节点虽带 slow+release marker，实际为本地 DuckDB/PYTHONPATH
  来源进程旅程；批准只运行修改的精确节点，无 package build、服务或 full release gate。
  证据须明确标记该局部范围，不能提升为 installed/package 或 release 资格。
- S5 offset Help 的原签名反射会暴露实现 `_Omitted`，批准窄改 dataset_render.py：
  从真实 typing.get_overloads 声明导出显示签名，无 overload 时保留原 signature。
  binding/registry 的实际实现签名和参数漂移校验保持不变，不硬编码 offset 显示签名，
  不增加 renderer shadow inventory；用独立 Help 断言确认两个公开 keyword 形状与预算。
- S4 保存里程碑：370 项本地专项通过（75.41s），覆盖 protocol/storage/publication/version、
  完整 Attribute、temporal Attribute 和 method_facts；12 文件 production/worker typing 通过。
  新 endpoint/report cold、direct/fact preview、required-map corruption 通过；
  补迁移的 relation receipt slow 节点 1 passed，带 release marker 的本地 DuckDB attribution
  cold 精确节点 1 passed，不作 release 资格。原有 18-file 消费者失败和 4 个 persist 节点仍在统一重跑。
- S4 core 扩展专项 293 passed，10-module typing、14-file lint 通过；
  shifted.align(shifted, match=ordinal()) 的实际探针发现 paired endpoint copy 保留 direct
  会使重命名端点与原 map owner 冲突。批准在新 paired 输出端点剥离已消费的 direct ancestry，
  保留完整 UTC/civil 端点目录及新 paired map；输入自身 direct 仍须完整校验，
  原输入不改写，不建立祖先重命名/组合框架。补窄回归后统一冻结。
- S4 consumer 集成最终 323 passed / 58 既有 marker deselected（245.35s）：
  全部 18 个 S3 consumer 模块、其余 Attribute/contribution/oracle/rules 及 4 个原 persist 节点。
  30 个旧 codec 失败全部消除，无针对 codec 的 skip/deselect 或断言删除。
- S5 预备部分已停止全部写入及运行检查，17 文件当前镜像冻结：
  `s5-partial-candidate.json` / `s5-partial-source-freeze.tar`，
  `c5a4f8eb99ba2dacb366ee9babff9f616c21e888158094a77777cfdbc6bd2566`。
  77 surface、30 temporal/terminal、8-module typing、16-file lint/import 通过；
  初始静态失败保留并修复。docs/site 仍未编辑，slow typing snippet 仍须执行；
  本部分只为 S4 提供稳定上下文，不代表 S5/PC16 或最终 gate 已验收。
- S4 core 与 storage 均明确停止写入，候选 `s4-candidate.json` / `s4-source-freeze.tar`，
  107 source/test 当前文件，`a72221a6a69a54fbd899af0514c44327b1aeaf687e1ba491344b4503c2df29e1`。
  三个组件（core14、storage24、S5 context17）全部摘要核对一致，HEAD 未变。
  `review_s4_core` / `review_s4_storage` 均为 Astra high，在同一个 S4 正式 R1 分工，
  各自核对全部 107 entry/archive/exit，S5 未完成部分不作为本轮验收。
  coordinator 107-file lint/import contracts 与 full-product typing（310 source files）通过。
- S4 正式 R1 两份报告已完成，全部 107 entry/archive/exit 摘要一致：
  `s4-storage-review-round1.md` No findings，独立 208 passed，valid-receipt 的范围外
  direct/comparison map 损坏拒绝及 interval codec/TZ-off 探针通过。
  `s4-core-review-round1.md` 两项 P2，独立 295 passed 之外的对抗探针复现：
  CORE-01 显式 ordinal align 的临时 DataAxis schema 仍带 CalendarComparisonBinding，
  与新 select 保留事实冲突；CORE-02 absent 映射未约束精确 baseline presence=false。
  CORE-01 有 accepted-S3 通过对照；CORE-02 在 S3 也复现，明确为 S4 所属完整性缺口，
  不误报为新回归。Frames 先写修复计划再统一修复，不重算数值/旧规则，
  保留原 schema 严格准入和最终 displaced presence-only facts，随后只允许最终 R2。
- S4 修复期间，S5 仅在 evidence/s5-docs-draft 的复制件起草当前 docs/API/ENZH 补丁，
  不修改实际 marivo/tests/docs/site 输入。该预备补丁待 S4 验收后由原 owner 应用并验证；
  不以草稿作为实际文档同步证据。S5 后续补跑已有 parquet relation 的四个本地冷旅程，
  检验其改写的 journey fixture，不提升为 installed/package 或 release 资格。
- S4 修复包先计划再实施，`s4-r1-repair-result.md`：临时 numeric view 只移除与虚拟轴
  不相容的内部 time facts，原始准入和最终 namespaced presence-only facts 保留；
  消费已验证 map 的 absent entry 约束精确基期标志，matched 分类缺行仍允许 false，
  ordinary displaced facts 不适用。415 tests passed（90.62s）、2-product typing、4-file lint 通过。
  原始六条 align 探针全部成功；原 corruption 探针在完整准入即拒绝，不能被 projection/Top0 隐藏。
  `s4-r2-candidate.json` / `s4-r2-source-freeze.tar`，107 文件，
  `d7e2972316212b9cd3eb3e8cc5a6883d25a5107102207d433c0260be96cff5ae`；
  对 R1 仅 4 文件变化，storage24/S5实际17/S2来源6 未变。两位原 reviewer 同轮执行最终 R2，
  不开启第三轮；S5 仍只编辑 evidence 草稿副本。
- S4 最终 R2 两份报告均 No findings：`s4-core-review-round2.md` 关闭两项 P2，
  独立 296 passed，六条原 align 探针及 interval/namespaced/original-input strict probes 通过；
  `s4-storage-review-round2.md` 独立 15 passed，valid-receipt 的错误 presence 在
  full/complete/n=1/export/scalar Top0 均拒绝，n=0/audit 无载荷。107 entry/archive/exit 一致，
  S4 以 2/2 轮验收，无未解决 P2+。S5 已正式释放实际文件，应用并验证预备文档补丁，
  完成 EN/ZH、Help/error guidance 和余下执行检查；先 site/API build，再由 coordinator
  执行未收窄 check-agent，避免共享生成目录并发。
- S5 已应用 32 份实际 docs/API/ENZH 改动；两个 latest root quick-start 仅路由到 guide，
  无需机械修改。README 的无关 Session/observe hunk 保持初始内容；Task 只改 period block。
  positive slow typing 精确节点与 local graph j2 的 produce/fixed/cold 三进程通过。
  parquet relation 四场景初跑 3 passed / 1 a06 stale paired-key oracle failed；
  当前轴应保留月份标签，owner 保留原始失败并修正实际合同预期后重跑。
  coordinator 同步 period design 为 implemented candidate，仍以本文 S5 状态限定验收范围。
  S5 冻结后再运行一次既有选定 PostgreSQL sparse-window witness，覆盖完成后的 schema 交接，
  不增加 provider 矩阵或发布环境。
- S5 最终 focused run 114 passed（24.70s），包含 surface、EN/ZH executable tutorial/time/statistics、
  temporal 与 terminal；positive slow typing 1 passed（6.95s）。relation a06 的旧 paired key/map-column
  预期按当前 calendar/typed consumed map 修正后 1 passed（10.59s），其余三场景初跑已通过；
  原始失败保留。graph j2 三个 source-offline 独立进程通过，均为 checkout local 证据。
  site verify:content 381 文件通过；npm build（含 docs-api、Astro 和 postchecks）通过，
  308 routes / 44 latest pages / 343 anchors。剩余静态/Help measurement 与清单正收口，
  尚未运行最终未收窄 check-agent 或 S5 正式审查。
- coordinator 为 README 准备独立 task-only commit image/patch：`task-readme-scope.json`，
  剔除 period block 后实际文件与 initial README 完全一致；提交只包含该任务 block，
  保留无关 Session/observe hunk 及其原版本文字，不为清理搜索覆盖用户已有内容。
- S5 最终开发组件冻结：50 authored paths（18 Python、32 docs/API/ENZH），
  `s5-candidate.json` / `s5-source-freeze.tar`，
  `42355f9878aada47298f7b93ffd060c931236514b230a15d27452cd5cc9d1206`。
  当前全部 108 source/test gate candidate 为
  `bb7d245970fb217f01d3ca3762065dfbdfee34b2dbc0d30413b296d445cda5c0`，
  `final-gate-source.json`；S4 runtime/core 除已交接 S5 文件外全部未变。
  未收窄 `make check-agent` 已启动，完整 lint/import 与 315-source typing 通过，默认测试进行中。
- 最终冻结源码的选定 PostgreSQL Runtime 重跑：`final-postgres-runtime.log`，
  1 passed / 1.85s，无 skip；与 S2 同一个 sparse-calendar/window witness，未增加 provider。
  任务启动的 PostgreSQL 和专用 VM 均已停止并保留数据卷，恢复开始时停止状态。
- 首轮未收窄 gate 未通过：`final-check-agent.log`，完整 lint/import、315-source typing 通过；
  default 5173 passed / 6 failed（364.72s，xdist 到达既有 maxfail 阈值后结束），API 阶段尚未运行。
  6 项位于 4 个剩余测试消费者：test_frame_effective_types 的动态 compare 旧 binary 调用，
  test_round1_api_fixes 的旧 directory 列 tuple、test_frame_evaluation 的旧 dtype slice，
  test_frame_execution 的旧 calendar fixture 缺 nominal_ordinal/civil columns。
  Frames / Storage 分别先写修复计划再迁移 1 / 3 文件，原始 raw gate 保留；
  不改产品以迎合旧 fixture，不删除原始 full-directory、Top0、float、shared-input 或 cold 断言。
  S5 正式审查仍为 0/2；修复专项通过后重跑未收窄 gate，不视为 S4 第三轮审查。
- broad consumer 修复已冻结：native 一文件完整 8 passed，calendar 三文件完整 64 passed，
  两包 lint/import 通过，所有原验证目标保留；只改 4 个测试文件，产品代码逐字节未变。
  原完整 gate 日志不覆盖，新未收窄运行 `final-check-agent-rerun.log` 已启动。
  此次修复不改变已成功最终 PostgreSQL witness 的产品源码、站点或文档；
  复验边界由 `final-gate-source-rerun.json` 记录。
- 四文件专项已全部通过，产品代码未变；在统一冻结候选上并行执行 S5 正式 R1 与
  未收窄 gate 重跑。两个结果都通过才接受 S5；若任一发现 P2+/失败，先合并修复，
  再做必要检查与最终 R2，不增加第三轮。审查期实际 source/tests/docs/site 全部冻结，
  coordinator 仅在本轮退出后更新本文 closeout 状态，原始运行记录继续写入 evidence。
- 未收窄 gate 第二次完成并通过：`final-check-agent-rerun.log`，5198 passed / 763 warnings
  （300.54s），完整 lint/import、315-source typing 与 API docs 全部通过。
- S5 同轮 R1 两份报告完成，146 workspace 与 intended archive entry/exit 摘要一致。
  `s5-surface-review-round1.md` 一项 P2 SURFACE-01：compare Help 漏写已支持的 matching-tick
  Duration 及绝对值/relative 语义；独立 33 passed 与 overload/drift/routes/CLI/Duration 探针通过。
  `s5-docs-review-round1.md` 一项 P2 DOCS-01：period design active body 仍有提案未实施、
  Help/版本未来切换措辞；独立 EN/ZH actual blocks 2 passed、8 对页面 AST 一致，
  stateful parser 确认 32 docs 的 140 Python fences 与设计 6 fences 解析、相对链接有效。
  原开发 regex 计数 139/2 excerpts 与独立 fence-state parser 方法不同，不当成新增语法故障。
  S5 owner 先写统一修复计划，恢复 Duration Help 并获批修订 coordinator design 的 active prose，
  历史原始设计/审查记录保留并明确标识；不改变算法或提前声称 S5 验收。
  修复冻结后仅最终 R2，并在修复源码重新执行未收窄 gate，禁止第三轮。
- S5 R1 修复包已冻结：`s5-r1-repair-result.md`、三个文件 manifest/patch/archive，
  `d8921bc62b85c737df64533e14c8a51c85c7d9cefb9e2ad68924ed441c0f90ae`。
  恢复 Duration Help 准入与 ticks/relative 说明并断言 19us；设计 active prose 改为当前候选合同，
  原 historical body 逐字节保留。33 focused passed（14.25s）、typing/lint、2 slow budgets、
  当前 checkout CLI/Python Help、6 design fences/links 通过；新 raw context 为 4977 chars / 4 pages / 0 repeats。
  除三个修复文件及 coordinator 的 post-R1 ledger 记录外，其余 142 个 R1 文件未变。
  来源/数值执行、align、type model 和 site 页面未变，保留已有 selected PostgreSQL 和 site 资格范围。
  在新统一候选上执行最后的 S5 R2 与未收窄 `make check-agent`，两者均通过才收尾；不进行第三轮。
- S5 owner `develop_disclosure` 已完成开发前计划和消费者分类：
  `evidence/period-comparison/s5-development-plan.md`；没有修改产品或执行实现测试。
  S3 迁移普通/numeric/statistics compare 行为测试；S4 迁移 cold worker；
  S5 迁移 surface/typing/packaging 有效调用及文档，不机械替换 align/Attribute 的合法布局。
- 实施中另观察到未跟踪 `docs/specs/public-class-surface-optimization-plan.md`；
  非本任务文件，同样保留且不暂存。
- 实施开始时没有实现测试、Runtime 或 release 证据；新增证据逐项记录在上方。
  最终运行未收窄 `make check-agent`；
  适用 Runtime、slow 冷读取和站点验证分别记录，不执行 release-check。

### 实施完成记录（2026-10-21）

- S0–S5 全部 accepted；每阶段正式审查不超过两轮，所有 P2+ 已关闭。
  S5 最终 `s5-surface-review-round2.md` 与 `s5-docs-review-round2.md` 均为 No findings，
  SURFACE-01 与 DOCS-01 已关闭。146 个任务文件的最终审查候选为
  `6698c7464fe798c46d1736e0e3b3ea8a4a072549232a3e02e033c4090426594c`；
  退出审查后仅更新本文与设计页头的完成状态和证据。
- 最终未收窄 `make check-agent` 通过：`final-check-agent-r2.log`，
  5199 passed / 763 warnings（301.64s），完整 lint/import、315-source typing 与 API docs 通过。
  复用已完成站点构建（308 routes、44 latest pages、343 anchors）、本地独立冷恢复旅程，
  以及 PostgreSQL 稀疏窗口代表 Runtime（1 passed，无 skip）；不追加验收范围。
  本次没有执行 installed-wheel 或 release-check，不代表发布验收。
- 提交范围为 146 个任务文件；README 只纳入 period comparison 的相关 hunk。
  无关 README 修改及其他未跟踪设计文件保留，原始审查和运行记录保留在 ignored evidence，
  正常 Git hooks 完成后创建本地 commit，不 push。

## 3. 各阶段改动范围

以下路径相对仓库根；`core/`、`dsl/`、`compiler/`、`binding/`、
`methods/`、`materialization/` 均位于 `marivo/analysis/` 下。
路径列出当前 owner，不要求为了匹配清单而拆分或新增模块。

### S0：先闭合有限数据合同

**范围**

- `marivo/_temporal_grid.py`、`marivo/_definition/types.py`：
  在现有时间坐标家族内区分名义日历与选定成员，容纳稀疏/空选择。
  常规 `time_scope.by` 的现有范围构造仍成立。
- `core/data_schema.py`、`core/frame_structure.py`、`core/frame_calendar.py`、
  `core/frozen_calendar.py`、`core/frame_data.py`、`core/artifact_data.py`：
  定义 origin/target 冻结目录、直接映射及无对应原因；定义命名比较说明与精确值列的绑定。
  将这些说明纳入 pending/complete schema、附件引用和结构校验，不保存来源图或可执行表达式。
- 分清桶 key、名义 ordinal、选定次序。不能把稀疏桶重新编号后拿新位置计算期间距离。
  `ordinal()` 仍遵守现有完整桶准入；“目录完整可解释”不等于“选定域连续”。
- 锁定唯一保存版本：实际实施基线目标为 Store 15、`marivo.frame_output/v4`、
  evidence `frame-output-v4`，在 S4 一起启用。
  已记录相对起草基线的变化；嵌套 schema 标识按实际合同变化调整，
  不给未改变的每个嵌套对象机械加版本。

**退出标准**

精确稀疏/空目录、直接关系与命名列绑定可以被类型系统和现有附件机制完整表示；
缺失必要附件、无效映射或重复键不能伪装为合法无对应。
用现有 `test_frozen_calendar.py` / `test_frame_structure.py` 补最小结构实例，
不另建通用关系框架。此时不宣称来源求值或保存已完成。

### S1：offset、shift 与日期对应

**范围**

- `marivo/_definition/correspondence.py`、`dsl/correspondence.py`：
  实现同一个 `offset` 的两个互斥参数形状，替换旧规则标签的生产和分派；
  保留 `CalendarCorrespondence` 家族和独立的 `ordinal()`。
- `marivo/_temporal.py`、`marivo/_temporal_grid.py`、`core/frozen_calendar.py`：
  实现 `CalendarAxis.shift` 与 source/data 两侧共享的期间对应语义。
  新 shift 准入 week/month/year 单位步长、完整桶或桶首前缀；数据侧已有完整 day/quarter 仍可匹配。
- 民用位置与实际 UTC 边界一起冻结；非零偏移的 gap/fold 拒绝。
  零偏移通过正常准入后精确复制已有 instant，不重新解析 aware fold。
- W53/不存在的截止日期记为合法无对应；权威缺失、越界、说明损坏是错误。
  shift 保留直接关系，不补包围范围、不合并 offset、不恢复链中已丢失的桶。
- 同步此阶段触及的 imports、规则消费者和既有测试；不新增旧工厂的兼容别名。

**退出标准**

PC02–PC06 的独立日期 oracle、PC12 的不可变/直接关系证据通过。
同时确认 `align(..., match=mv.offset(...))` 复用新规则但保留 paired 语义，
没有因 compare 改造而删除完整 day/quarter 或 `ordinal` 能力。

### S2：让显式 observe 正确消费选定时间域

**范围**

- `dsl/frame_sources.py`、`dsl/source_capture.py`、`binding/observe.py`：
  保持 `observe` 签名，将精确选择与直接关系传入捕获、pending schema、source binding 和结果。
  空日历不能被当成无时间轴的 scalar 观察。
- `binding/windows.py`：空域产生零端点；完整桶的 `trailing_periods` 从名义日历取前驱，
  保留已有完整桶限制。原 Metric.window 继续拥有各端点的贡献窗口。
- `materialization/query_preparation.py`、`compiler/observation.py`、
  `compiler/original.py`：分开输出成员与窗口所需贡献。
  稀疏间隙不能参与无关的分类发现、生成 null 时间桶或触发不在本问题中的数值检查。
- `compiler/graph_plan.py`、`methods/local.py`：
  按精确选定时间域补齐真实的普通联合分类；不把各普通维度分别做笛卡尔积。
  零行仍保留类型、目录和无对应说明。
- `marivo/_definition/relations.py`、`materialization/population_preparation.py`、
  `core/frame_source_facts.py`：CalendarAxis 转有限 Population、来源事实和 frozen 目录同步。
  `by=P` 仍由调用方提供各端点的输出域；贡献 `where=in_population(P)` 原样使用，不自动平移或重选。

继续复用 `materialization/graph_source_execution.py` 的现有来源准备与执行边界；
只在必要处修正消费，不增加 provider 能力框架。

**退出标准**

使用真实本地 DuckDB 来源验证 PC03/07/08/09：
空基期零行、稀疏周不补桶、间隙不制造类别；
AOV、distinct、分位数按各端点原贡献重算，窗口距离和硬日期过滤正确。
来源结果带齐 S1 的冻结说明，供 S3 自动关系使用。

### S3：命名多基期 compare

**范围**

- `dsl/frame.py`、`core/frame_expression.py`、`compiler/query_program.py`、
  `compiler/frame_plan.py`、`compiler/graph_plan.py`：
  为 compare 建立当前输入加有序命名基期的 N+1 operation/program/schema 路径。
  复用现有 tuple inputs、输入共享和 scheduler；不循环调用旧 binary compare 后拼表。
- `materialization/query_runtime_types.py`、`materialization/frame_evaluation.py`、
  `materialization/graph_local_execution.py`、`methods/frame_comparison.py`：
  消除 compare 的二输入假设，复用完整键检查、期间对应和现有 nullable 数值核。
- 一次构造本期轴和共享当前列；依次加入 `<alias>.baseline.*`、
  `<alias>.absolute.*`、`<alias>.relative.*` 与 presence。
  按设计校验相同非空值路径、类型/单位、同 Session 和完整轴绑定。
- 每个基期独立解析 `on` / `match`；仅当直接 origin 目录与当前轴相符时自动使用关系。
  显式 match 与可用直接关系冲突必须失败；不跨链追溯或推断业务名称。
- exact 比较实际联合域；outer 按当前行及基期声明次序扩充联合键。
  先拒绝无法映到本期域的实际基期时间行，再执行数值运算。
  必需检查不得被后置 select 或 Top-0 剪掉。

**退出标准**

PC01/08/10/11/15 通过；同表与独立命名比较结果一致，共享当前依赖不重复消费。
`align` 继续使用二输入、left/right 和 paired 输出；只移除 compare 专属的旧布局分支。
S3 可先用 S1 构造的固定输入验收，来源端到端以 S2 集成结果为准。

### S4：后续运算、保存与冷恢复

**范围**

- schema inference、`core/frame_structure.py`、`materialization/pandas_export.py`、
  `materialization/graph_reading.py`：实现设计 §8 的精确传播。
  where/top 保留解释目录；select 随删列、改名更新绑定；
  reduce 按现有值语义聚合、丢弃 presence 和不再成立的比较列组结论，
  移除时间轴时丢行级关系，仅保留审计事实。
  align/再次 compare 消费已有固定值，不获得原 Metric 重算能力。
- `materialization/` 下的 `terminal_protocol.py`、`terminal_publication.py`、
  `terminal_storage.py`、`terminal_store.py`、`store.py`、`layout.py`，
  以及同目录的 `frame_services.py` / `graph_reading.py` 读取入口：
  一起切换 S0 确定的 Store、输出 envelope 和 evidence 格式，
  同步闭合 codec 字段、SQL evidence 常量、准入错误及测试。
- 必需附件覆盖完整读取和部分预览；使用已有发布事务，不增加恢复任务、双读或迁移器。
  旧 Store 原样保留并明确拒绝恢复；不得从当前 Catalog、来源或 TZ 规则补齐旧记录。
  `PairedDataAxis` 及其 codec 继续服务 align。

**退出标准**

PC11/13/14 通过；同一数据集的 pending/memory/stored 输入产生相同的命名比较结果。
一条新进程冷读取旅程同时保存并恢复本期、shifted 基期和完成报告：
先对恢复的端点 Frame 省略 match 执行命名 compare，确认直接关系仍可消费且与 pending/memory 对照一致；
再读取完成报告，验证值、presence、冻结边界及后续 select/where/reduce。
全程禁止来源、模型和当前 TZ/日历重建。补一个必需映射损坏反例及旧版本准入反例。
此证据证明保存合同；日期/数值正确仍由 S1–S3 的独立 oracle 证明。

### S5：公开切换、旧接口清理与统一验收

**范围**

- 完成 §4 的消费者清理；公开签名、typing、exports、Help、错误建议和实例披露只呈现新合同。
- `marivo/analysis/_public.py`、`__init__.py`、`dsl/_result_disclosure.py`、
  `dsl/_disclosure.py`、`operators/_disclosure.py` 及受影响的时间/Population 披露 owner：
  接入 `analysis.offset`、`analysis.calendar_axis.shift`、`analysis.analysis_frame.compare`，
  更新有界 `.show()`、类型导航和 CLI 例子，不建立额外 renderer 清单。
- 更新本目录 owning specs、`docs/api/analysis.rst` 和当前示例；
  同步 `site/src/content/docs/docs/latest/` 与 `site/src/content/docs/zh-cn/docs/latest/` 的
  quick start、time-and-cohorts、comparison-and-attribution、statistics、troubleshooting。
  文档变更描述最终合同，不把过渡态写为兼容入口。
- 完成 §5 的验收并更新阶段证据。最多两轮正式实现审查；
  保留原始发现与修复，未解决 P2+ 阻断收尾，不增加第三轮循环。

当前只读搜索未发现 packaged skills 消费这两个旧规则名或 compare 调用。
不为本任务预设 skill 改造；若实施中发现确需修改其工作流，先完成其他范围，
再列出具体改动并取得该文件的明确授权。

**退出标准**

PC16、PC01–PC15 的必要证据和未收窄的 `make check-agent` 通过；
相关 Runtime 如适用，单独报告结果或阻塞。
没有未解决 P2+、旧公开别名或被替代输出分支；不把文档检查当成实现、Runtime 或发布验收。

## 4. 旧接口与消费者清理

在所属阶段替换生产者与消费者，S5 统一核对；不把“以后再移除”作为完成状态。

| 清理对象 | 动作 / 阶段 | 必须保留的边界 |
| --- | --- | --- |
| `previous_year()` / `period_offset()` | S1 替换规则构造及内部 kind 分派；S5 清除 exports、typing、Help、有效例子中的旧调用 | `offset(years=-1)` / `offset(periods=n)`；不留重导出或包装别名 |
| `frame.compare(other_frame, ...)` | S3 改为非空命名映射；迁移单基期消费者 | 单基期也必须显式命名；不接受旧输入再自动命名 |
| compare 的 left/right/absolute/relative 顶层布局 | S3 删除专属 schema、整形、值路径和 presence 预期；S4 更新保存/导出 | align 的 left/right、paired 轴及现有一般时间并集继续保留 |
| compare 与 align 共用的二输入假设 | S3 分开 compare 的 operation/program/schema；删除不可达旧 compare 分支 | 复用键/对应/算术核和 scheduler；不复制一套基础设施 |
| 连续 ordinal、非空 endpoints、包围范围即成员域的假设 | S0–S2 修正本链实际消费者 | 常规连续日历及完整桶 trailing_periods 的原合同 |
| 旧 Store / 输出格式读取 | S4 一个版本准入清切换；更新 writer、reader、SQL 常量和拒绝错误 | 不删除用户旧 Store，不迁移、不双读、不自动重建 |
| 旧 API 测试与当前文档 | 各阶段迁移行为断言，S5 同步 surface snapshots 与 EN/ZH | 旧 release notes、历史验收记录可保留原貌；不能当作当前教程 |

清理搜索覆盖 `marivo/`、`tests/`、`docs/api/`、当前 owning specs 和 latest 的有效教程。
逐条分类 `previous_year`、`period_offset`、旧 compare 调用、旧输出路径的命中；
历史说明、退役错误测试、align 合法用法不作机械替换。
不因测试文件仍叫 `test_query_correspondence.py` 就做无关重命名。

## 5. 最小验收集与执行顺序

不构造“全部粒度 × 全部 dtype × 全部载体 × 全部 provider”的矩阵。
每条关键语义有直接证据；既有测试已覆盖的数值政策迁移输入和输出断言，不重复建套件。
共用 `tests/conftest.py`、`tests/shared_fixtures.py` 及已有窗口/来源 fixture。

下表文件位于 `tests/analysis/`，surface 和 typing 路径另列：

| 验收组 / 阶段 | 覆盖设计 ID | 主要现有落点 | 最小新增或迁移证据 |
| --- | --- | --- | --- |
| 日期与直接关系 / S0–S1 | PC02–PC06、PC12 | `graph/test_frozen_calendar.py`、`materialization/test_frame_calendar_matching.py`、`temporal/test_timezone_observation.py` | ISO W01 对前 52 周差异；W53 稀疏/全空；完整闰月与不存在截止；MTD/YTD；一个非零 DST 拒绝与零偏移 aware fold；不可变直接链 |
| 原贡献与时间域 / S2 | PC03、PC07–PC09 | `materialization/test_deferred_windows.py`、`test_observe_regressions.py` | 一个共享真实来源数据集验证 AOV/distinct/分位数原贡献；稀疏 trailing_periods、零端点、间隙分类、联合分组与显式 P |
| 比较与严格执行 / S3 | PC01、PC08、PC10、PC11、PC15 | `materialization/test_frame_correspondence.py`、`test_frame_calendar_matching.py`、`graph/test_frame_plan.py`；既有 numeric compare 测试 | 单/多基期同表；完整键、缺期/行/NA/零；outer 顺序和域外行拒绝；on/match 冲突；共享输入；现有 Decimal/Duration/负基期/溢出断言；删列/Top-0 不吞错误 |
| 组合与保存 / S4 | PC06、PC11、PC13、PC14 | `graph/test_frame_structure.py`、`materialization/test_frame_storage.py`、`test_frame_correspondence.py` | 一组运算传播断言；同数据三载体输入比较；一条冷读取旅程含恢复端点后省略 match 的 compare 与完成报告读取；一个映射损坏、一个旧版本拒绝 |
| 公开合同 / S5 | PC16 | `tests/surface/` 的 exports、imports、Help、period_alignment_guidance、disclosure_content、help_attributes、context_budget；`tests/typing/analysis_dsl_public_contract.py` | 唯一入口和完整导航；错误建议有效；示例可执行；有界披露；EN/ZH 一致；有效消费者不残留旧形状 |

日期期望使用 stdlib 或明确列出的民用/UTC 边界；数值期望从原始贡献独立计算。
不能用实现自己的 shift/match 结果生成 oracle，再证明自己正确。
按设计保留少数有意义的拒绝例子：跨 Session、错误锚点、paired compare 输入、
部分 day/quarter、缺失附件；不扩展任意损坏字段或无关输入组合。

### 检查顺序

1. 各阶段先运行对应文件的 `make test TESTS='...'`，
   修改 Python 同时运行针对实际模块的 `make typecheck TYPECHECK_TARGETS='...'`
   和 `make lint-agent LINT_TARGETS='...'`。新增或迁移测试必须测试行为，不镜像私有实现。
2. 相关冷读取或其他必要 case 若标记为 slow，使用 `make slow-test TESTS='...'` 单独执行；
   default gate 不包含它们。测试名含 runtime 不代表具有 Runtime 资格，按真实 marker 和来源分类。
3. S5 在最终候选上运行一次未收窄的 `make check-agent`，
   覆盖 lint、typing、默认测试与 API 文档；若失败，先定位和修复、通过受影响检查，
   再重跑最终门禁。通过后只在有代码变更或未解决问题时重跑，不能沿用旧候选的通过记录。
4. latest 文档执行现有站点 content 校验，运行本次新增/改写的公开示例，
   并核对 CLI/Python Help 共用 registry。复用已有 API 文档检查结果，不另建文档验收工具。
5. S2 若改变共享 Ibis SQL 形状或 source qualification，
   在 `numeric/test_remote_numeric_query.py` 既有 Runtime 设施中选受影响的代表 provider 验证；
   选择依据和未覆盖范围写入证据。无需每个粒度重复每个 provider。
   本任务不执行 `make release-check` 或为普通实施启动发布环境。

最终证据分开记录静态/本地、slow 冷读取、适用 Runtime 与未执行的 release gate。
计划起草时没有上述执行证据；实际阶段状态以 §2 的持续记录为准。

## 6. 历史计划文档记录

| 工作 | 状态 | 证据 |
| --- | --- | --- |
| 对照设计与当前代码落点 | complete | 两个只读探索工作包分别核验时间/来源与 Frame/保存链；主代理核验公开消费者、命令和基线 |
| 起草阶段、依赖、清理与验收 | complete | 本文 S0–S5、§4、§5；沿用设计 PC01–PC16 |
| 独立计划审查 | complete | R1 发现 1 项 P2；R2 定点确认已修复，无剩余 P2+；只检查本计划，不重开已定设计 |
| 文档链接、编号、范围及 diff 检查 | complete | 三份相关文档的 38 个本地链接、设计中 6 个既有 Python 示例 AST、围栏/whitespace、PC01–PC16 覆盖与 S0–S5 planned 状态通过；tracked 与新文件 diff 检查通过 |

以下为实施前的计划审查；它本身不改变 S0–S5 的 planned 状态。

| ID | 级别 | 原始发现 | 修订 |
| --- | --- | --- | --- |
| R1-P1 | P2 | 只冷读完成报告，可能漏检 shifted 观察结果保存时丢失直接关系，导致恢复端点后无法自动 compare | S4 与验收表在同一冷读取旅程增加恢复本期/基期后省略 match 的命名 compare，并对照 pending/memory；不新增旅程或载体矩阵 |

计划 R2 已确认该修订及附近合同一致，无新增发现。
该历史文档轮次没有运行实现测试、Runtime 或 release gate；实施证据另见 §2。
