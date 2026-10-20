# Session 与 observe 重设计实施计划

Status: implemented，2026-10-20。实施、审查与最终验收已完成；实际任务和证据见
[实施进度记录](session-observe-implementation-progress.md)。

目标合同：[Session 与 observe：显式求值域重设计](session-observe-redesign.md)。
本计划采用该稿最新的 `Population.count()` 和创建时捕获系统时区方案；
[审查记录](session-observe-redesign-review.md) 中旧冻结稿的结论不外推到这两项后续修订。
代码核对基线：`622a930b7adba834744ad285789782e1d4d6e6b2`。
正式实施前记录届时 HEAD 与工作树差异，保留其他任务的改动。

## 1. 目标与实施原则

完成一次整体 breaking cutover：

- `observe(metrics, *, by=(), where=None)` 以 `by` 为输出求值域，时间域也在其中。
- `by=Population` 只返回指定完整键，并保留域内无事实坐标。
- `TimeScope` 产生 interval 轴；calendar 保留自己的分桶与窗口资格。
- `P.count()` 捕获 P 的完整身份域，作为可组合的惰性 Metric。
- interval 可参与结果操作、显式期间匹配、持久化和离线冷恢复。
- Session 改为严格 create、精确 ID 恢复和只读发现，删除隐含 current。
- 同步公开类型、Help、错误、文档、示例及实际消费者。

按用户要求控制实现和验收规模：

1. 复用现有定义捕获、compiler、scheduler、数值内核和发布事务；在拥有者处补齐合同。
   一个检查放在其负责边界，内部沿用已验证的类型，不在每层重复验证相同事实。
2. 直接替换旧合同，删除失去消费者的旧分支。没有旧参数兼容层、旧 Store 双读、
   自动迁移、新执行框架、通用时间轴插件系统或任意域选择 API。
3. 先保证指定域、窗口和分配的数值正确；仅在已有合格路径内下推完整键。
   不为本次切换另开性能优化项目，也不以逐单元格循环发查询代替现有计划。
4. 扩展已有 fixtures 和 owning 测试，用少量反例覆盖真正变化的语义。
   沿用设计 O01–O29，不再复制一套验收编号、全排列矩阵或证据平台。
5. 类型、完整键、所有权、分配分母和保存数据完整性是既有必要合同，仍须成立。
   验收重点是这些合同在变更处是否保持，不重做未变化模块的全部资格证明。

计划起草时仅新增本文；实施阶段的公开面切换仍受仓库授权规则约束：
如需修改 `marivo-analysis` 或 `marivo-semantic` 打包技能，届时取得具名授权；
本计划不授权修改 `AGENTS.md`、提交或发布。

## 2. 实施前差距与代码落点

计划起草时，代码与 Live Help 仍公开 `during`、`members` 和 `member_count()`；
`MemberCount` 不携带 Population，观察绑定只持有一份全局成员上下文。
来源完成路径仍处理 `__query_population_uncovered` 覆盖错误。
结果时间结构与配对端点主要面向 calendar，保存基线为 Store 12 /
`marivo.frame_output/v1`。因此本次需要贯通捕获、求值、数据结构与保存，不能只改签名。

下表是已核对的职责落点；实施时按真实消费者确定修改文件，不要求逐文件改动。

| 职责 | 现有落点 | 主要变化 |
| --- | --- | --- |
| 输入与依赖捕获 | `marivo/analysis/session/core.py`、`dsl/{source_capture,frame_sources,population}.py`、`core/frame_key_inputs.py` | 新 by、P.count()、多个域及 Frame 依赖 |
| 中立定义与表达式消费 | `marivo/_grouping.py`、`marivo/_temporal.py`、`marivo/_definition/{types,expressions,deferred,observations,graph}.py`、`marivo/semantic/_deferred_semantics.py` | interval 闭合变体、带域计数、指纹；保持依赖方向 |
| 观察、窗口与执行 | `marivo/analysis/binding/{observe,windows,sampling,coordinates}.py`、`compiler/{observation,graph_plan}.py`、`materialization/{query_preparation,query_key_fusion,graph_source_execution}.py`、`methods/local.py` | 指定域完成、计数分派、贡献范围与输出域分离 |
| 分配 | `marivo/analysis/binding/membership.py`、`materialization/{membership_preparation,population_relations}.py` | 先保留原分配合同，再选择输出桶 |
| 结果与匹配 | `marivo/analysis/core/{data_schema,frame_structure,frozen_calendar,frame_expression}.py`、`dsl/frame_axis.py`、`methods/frame_comparison.py` | interval 目录、等值合并、ordinal、配对端点及数据操作传播 |
| Session 与保存 | `marivo/analysis/session/`、`materialization/{admission,store,layout,reconciliation,terminal_protocol,terminal_storage,terminal_publication}.py` | 严格创建、取消激活、一次格式切换、冷恢复 |
| 公开消费者 | `marivo/analysis/{__init__,_public}.py`、`marivo/analysis/_capabilities/`、`marivo/introspection/`、`marivo/telemetry/__init__.py`、`tests/surface/` | 导出、Help、修复、遥测和文档一致 |

表内 `marivo/`、`tests/` 路径相对仓库根，其余缩写路径相对 `marivo/analysis/`。
`frozen_calendar.py` 是现有目录与匹配职责的定位，不要求将 interval 命名为 calendar；
只在职责确有需要时拆出小型中立模块。

## 3. 实施顺序

| 阶段 | 工作 | 依赖 | 可核验产出 | 状态 |
| --- | --- | --- | --- | --- |
| S1 | 输入、时间域与计数依赖 | 最新设计与当前基线 | 类型与捕获闭合，构造不读来源 | 已完成 |
| S2 | 指定域求值、窗口、分配及计数 | S1 | 代表性数值反例通过 | 已完成 |
| S3 | interval 数据操作与期间匹配 | S1、S2 | 完整目录与实际行分离，配对正确 | 已完成 |
| S4 | Session 生命周期与保存格式切换 | S1–S3 | 新格式发布、精确恢复、旧格式拒读 | 已完成 |
| S5 | 公开披露与调用方迁移 | S1–S4 | 新合同只有一个入口，文档与消费者一致 | 已完成 |
| S6 | 最终候选验收 | S5 | 聚焦证据、完整常规门禁及必要交付边界证据 | 已完成 |

阶段用于安排依赖，不设逐阶段签字、额外审批或独立验收运行器。
随阶段修改相邻调用方和测试，S5 再统一检查公开面；中间状态不作为可交付版本。

### S1：输入、时间域与计数依赖

1. 更新 observe 捕获签名，删除独立 during/members 通道。把 TimeScope 规范化为
   interval 分组；tuple 与 Population 共用唯一输出时间轴检查。
   保持普通联合坐标，拒绝 tuple 混入 Population 或重复时间轴。
2. 为 Grouping、坐标绑定和结果 schema 增加具体 interval 变体。记录精确边界与规范化
   边界时区，并区分声明目录和实际键。`product` 接受 TimeScope，仍至少两个因子；
   用现有零轴单元域表达单时间因子，不新增构造器。
3. 实现 `Population.count() -> Deferred`，计数节点直接携带完整单 Entity 身份域。
   使用现有中立定义表达依赖，不让 semantic 层导入 analysis 执行对象。
   每个 P/Q 独立捕获，只有计数的公式也必须进入 Population 求值计划。
4. 将计数域的 FrameKeyInput、所有权和定义依赖纳入现有遍历及统一调度。
   算术、声明 Metric 展开和窗口包装后不能丢失域依赖；不调用隐藏 compute。
   删除无域 ContextMetric 和单一全局成员假设的实际消费者。
5. 更新指纹对新域语义、时间种类、计数依赖及分配合同的编码。
   不保持旧 observe 指纹，也不增加另一份可变上下文。

完成依据：聚焦构造/类型测试覆盖两种 by、P/Q 计数、非法计数域、跨 Session，
并确认构造没有业务读取或 Run。复用
`tests/analysis/session/test_observation_binding.py`、`tests/surface/test_analysis_typing.py`。
这些检查仅证明捕获与类型，不提前证明数值和保存。

### S2：指定域求值、窗口与分配

1. 将 by=P 的“全域覆盖检查”改为“仅在 P 的完整键上完成结果”，移除对应 uncovered
   标记和错误路径。保留域内必要字段、版本、根、采样与来源资格检查。
   不把所有准备错误一起删除，也不为证明 P 为空扫描全来源。
2. 区分普通 tuple 的联合分组发现、时间桶补齐和 P 的显式完整域。
   时间加分类从相关贡献及合法计数分类取得真实联合键，再补时间桶；
   空分类域为零行，时间单轴保留无事实桶。零轴 `{()}` 与 `{}` 分开处理。
3. 从唯一输出时间域取得端点。普通 interval 聚合使用 `[start,end)`；
   窗口按自身依赖读取历史，显式硬时间条件继续生效。
   复用旧标量 to_date 的端点规则支持 interval，拒绝在 interval 上使用 trailing_periods
   或在无时间轴时使用窗口；calendar 的完整桶、reset containment 和认证前驱规则保留。
4. 分配保持原贡献身份、关联元组及原分母，完成映射后选择 P 中的桶。
   多事实根独立规约后组合；内部样本、mean/ratio/weighted/distinct 按原指标求值，
   不把细组最终值重聚合成所选域指标。
5. 每个计数域独立求值并按合法同 Entity 分类定位，按时间桶重复使用。
   P 中无事实身份仍计数，空 P 得 0；必需分类读不到时失败。
   where/window 只绑定事实规约；只有计数却附加无效果条件或窗口时明确拒绝。
6. 固定 Frame 桥接继续固定键，pending 选择正常求值，来源新值不承诺旧快照。
   空 P 的生产失败正常传播；已知空字面域在必要静态准备后可跳过无用业务读取。

完成依据：使用第 4 节的指定域、窗口/分配和计数反例。
主要扩展 `test_query_results.py`、`test_population_membership.py`、
`test_deferred_windows.py`、`test_named_calendar_windows.py`
（均在 `tests/analysis/materialization/`）及相关 numeric/temporal 测试。
现有下推路线若受影响，保留针对计划依赖与数值的少量断言；
不以一个后置过滤掩盖已改变的窗口或分配结果。

### S3：interval 数据操作与匹配

1. 贯通 pending/memory 数据结构、FrameAxis.role、坐标附件、完整 pandas 导出和展示。
   interval 目录每条记录拥有精确 start/end 与边界时区，局部 token 不作跨结果身份。
   两个区间可重叠或有不同边界时区，不强加日历分区约束。
2. 等值对齐按精确区间身份合并去重目录；实际输出只取实际完整键。
   where/top/select 及保留时间轴的 reduce 保留完整目录，删除轴才删除其目录。
   不因筛选重编号、缩减声明域或补出目录中未出现的行。
3. 为 interval 对 interval 扩展显式 ordinal：两侧各一条声明记录、边界时区一致。
   扩展现有配对端点数据类型与映射，不把 interval 装进 calendar 描述。
   保持已配对轴不可再次匹配；不新增 offset/year 或多区间 ordinal。
4. 唯一 interval 轴可推导轴对，时间加分类仍要求 on 覆盖全部轴。
   零轴、interval、calendar 不隐式转换或广播；exact/outer 看实际键，
   presence 与值 NA 分别处理。

完成依据：扩展 `tests/analysis/materialization/test_frame_correspondence.py`，
覆盖同区间等值、不同期间 exact/outer/ordinal，以及 O29 的
“重叠区间 outer → 筛成一行 → 继续等值成功、ordinal 拒绝”组合。
同一例子在 S4 复用作冷恢复断言，不另造一套目录测试。

### S4：Session 生命周期与保存格式切换

Session 改造：

- `get_or_create` 替换为严格 create：原子创建，不覆盖同名；错误给出现存真实 ID。
  name 拒绝空白和两端空格；question 为创建时可选关键字，domains 规范化后冻结。
- `timezone=None` 复用现有系统时区解析，创建时保存名称及解析方式；
  显式非法值不回退。实例公开 timezone，删除旧公开时间属性，
  instant/time_scope 默认消费保存值。
- resume/inspect 仅接收精确 Session ID。resume 只恢复已有 Store，
  复用 writer guard；无恢复工作时不更新元数据。recent/inspect 保持有界只读。
- 删除 current、activation 及相应 Store 状态；updated_at 只记录已提交状态变化，
  纯 resume/inspect/compute 不刷新。abandon_run 改为 reconcile_run，
  保留原事务核对、归属和活动 writer 检查，不重算来源。
- `members(entity, ...)` 接收 resolved Entity；保留实际身份/版本/时点合同，
  不因同路径原定义版本变化单独拒绝。`values` 的 by 默认 `()`。
- 对已有类型事实足够的无来源 frame 和 `population_from(..., on=())`，
  去掉无必要的 Catalog 加载；需要字段/身份语义的路线仍正常绑定。
  普通桥接资格不扩展到 interval/calendar/paired。
- 删除 source_bindings、graph 及其无其他消费者的专属类型/模块；
  read、persisted_frame、runs/get_run、render/show 按设计 §9 保留。

保存改造：

- 将 interval 目录、键引用和配对端点纳入现有闭合 codec、附件和收据校验。
  完成数据不保存可执行 TimeScope、Metric、Population 源图或生产者回调。
- 合并本次时间结构与 Session schema 变化，一次升级 Store generation 和输出协议；
  实施时按当时基线分配版本号，不为中间阶段多次占号。
- 复用 compute/persist、writer guard、资源日志与发布事务。compute 不创建发布 Run；
  persist(memory) 发布已有值，恢复按原事务判定，不重读来源。
- 旧格式明确拒读且旧字节不变，不绕过 generation 新建同项目 Store。

完成依据：扩展既有 session/discovery/timezone 测试和
`tests/analysis/materialization/test_frame_storage.py`。
一条新进程冷恢复旅程覆盖保存目录、presence、等值与 ordinal，禁用模型和来源；
一个损坏 interval 附件用例接入既有完整性检查。
沿用现有事务、并发和故障测试，仅补 changed schema/recovery 行为所需断言。

### S5：公开披露与消费者迁移

1. 同步签名、具体类型、docstring、导出快照、native Help、CLI、show、结构化错误和遥测。
   删除旧入口及其建议，不保留 Help 别名或幽灵能力 ID；保留方法和属性也逐项核对。
2. 迁移仓库调用方：during 改为时间 by；原 members 贡献筛选改为闭合
   in_population 条件并保留原 where；隐含身份补域改为显式 by=P；
   member_count 改为 P.count。已有时间轴的 P 不重复乘时间域。
3. 同步当前拥有者文档：Python Analysis Design、AnalysisFrame、Session State、
   Timezones and Calendars，以及受影响 DSL/执行/示例/切换说明。
   更新 `site/src/content/docs/docs/latest/` 与 `site/src/content/docs/zh-cn/docs/latest/`。
   历史审查和验收记录保留历史含义，不做全仓字符串替换。
4. 对齐 `attribute-design.md` 与 `attribute-implementation-plan.md` 的期间输入、
   所选域及示例；明确父域共同输入和贡献守恒仍是 Attribute 自身合同。
   不在此任务实现 Attribute，也不沿用其中旧“域外贡献必须报覆盖错误”的前提。
5. 检查两份 packaged skills 是否存在冲突流程；有实际冲突时在获得具名授权后最小修改。
   缺少必要对齐时列明具体项，不把公开切换标为完整。

完成依据：复用 surface 的导出、Help 可达性/预算、属性与 typing 测试；
最小公开示例能走通 create → observe → compute/persist → resume → compare。
旧参数迁移必须检查新的轴结构和比较意图，不只让调用不再报 TypeError。

### S6：最终候选验收与交接

记录最终候选与实际差异，完成下节必要检查。
若检查后修改代码，只重跑受影响聚焦检查，并保证最终常规门禁对应最终代码。
没有新变更、失败或未解疑点时不反复重跑已通过的检查。

交接说明实际完成的阶段、O 编号对应证据、格式版本、公开对齐及剩余缺口。
计划状态、历史绿色记录和静态 SQL 编译不能替代本次实现或 Runtime 证据。

## 4. 必要验收，按变化合并

O 编号直接引用设计 §12。一条场景可承担多个 O 编号；
已有测试能够证明的行为只迁移调用，不重复新建测试。
数值使用手工小表或独立 SQL 作为期望，检查相关完整键、值、NA、presence、类型/单位。

| 组合场景 | 关键断言 | 设计覆盖 |
| --- | --- | --- |
| 输入与公开切换 | 新签名、旧入口删除、所有权、同路径版本变化；保留公开入口一致 | O01、O27、O28 |
| 指定域与空域 | P={1,3,999} 只得 10、20、null；East/Web 与 West/Store 不扩成四组；零轴单元/空域、空 P、无事实桶、all-null sum 区分 | O04、O05、O06、O23 |
| 来源依赖与原指标 | 固定 Top 不重排、pending 会重求值；投影地区不恢复客户选择；多根无 fanout；mean/ratio/weighted/distinct 保持原定义 | O07、O08、O09、O18 |
| 时间与分配 | scope 总量有 interval 轴、一桶不 squeeze；跨月回看保留历史且硬谓词有效；保留日历边界测试；100 各半分配选 A 得 50 | O02、O03、O10、O11、O12、O14 |
| 显式计数 | 名单 3、实际来源身份 2、收入 30、每人 10；空 P 为 0；P/Q 独立；时间重复、分类缺失失败、事实过滤不改分母、无效果条件拒绝 | O15、O16、O17 |
| 时间结果组合 | time×unit 可构造；筛选保留目录；exact/outer/ordinal、轴种类、presence、O29 重叠目录组合；不开放时间轴回源 | O13、O19、O20、O29 |
| Session 生命周期 | 同名不覆盖、精确 ID、只读发现、必要恢复；TZ 变化不影响恢复时区，domains 冻结，updated_at 无 activation 副作用 | O21、O22 |
| 完成、发布与冷恢复 | compute 无 Run、persist 不重读；值/轴/目录/presence 往返；无模型来源恢复；损坏附件报错、旧格式拒读且字节不变 | O24、O25、O26 |

检查分四层，复用现有入口：

1. **阶段聚焦检查**：修改测试前读取 `marivo-test-fixtures` skill，复用
   `tests/conftest.py`、`tests/shared_fixtures.py` 与领域 fixtures。
   运行实际改动对应的 `make test TESTS='...'`、
   `make typecheck TYPECHECK_TARGETS='...'` 和 `make lint-agent LINT_TARGETS='...'`；
   typing 包括实际修改的 `_definition`、semantic 模块。
2. **最终常规门禁**：共享行为变化需覆盖默认测试；最终运行不带缩窄变量的
   `make check-agent`，它已包含 `make test`，无需紧接着再重复同一默认全套。
3. **相关 Runtime**：用 `make runtime-test TESTS='...'` 运行实际受影响的完整键传输、
   窗口或分配路线。优先现有 remote numeric/temporal 测试及已配置 provider；
   覆盖发生变化的各执行分支，不做全部 provider×全部 O 场景排列。
   未运行的路线列明未验证，不以本地测试或另一 provider 通过推定资格。
4. **交付边界**：构建并安装一个候选 wheel，复用一个组合旅程检查公开入口及离线冷恢复。
   被标记 slow 的必要新进程用例单独运行。复用已有包构建与进程支持，不新增验收框架；
   不运行完整 `release-check`，不启动无关 MinIO，不发布包。

正式实施时直接更新本计划的阶段状态，并在下方追加实际负责人、修改范围、
O 编号到测试的对应、检查命令/结果及未解决失败。复用测试输出位置，
无需预建空证据目录、逐项审批记录或另一份大型进度矩阵。

## 5. 实施记录

实施记录由 [进度记录](session-observe-implementation-progress.md) 统一维护。
本文阶段表保留初始计划，实际状态、原始发现及验证结果以进度记录为准。
