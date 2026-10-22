# 时间 authority 与源时间过滤实施计划

日期：2026-10-22。状态：已授权 agent 团队完整实施；实际进度和证据见
[实施账本](temporal-timezone-filtering-progress.md)。

设计依据：[Time Authority and Source Time Filter Optimization](temporal-timezone-filtering-proposal.md)
（下称提案）。用户已在后续指令授权按 agent-team-development 完整实施。
用户本轮补充：`hour_prefix` 继承 prefix 的 timezone，不增加独立 `timezone` 参数。

## 1. 基线、范围与成功标准

本轮检查 HEAD：`27f60de80f0a5a394e51b2a42fa95fb69c64bb76`。
开始时工作树仅有未跟踪的提案文件；本轮保留该文件并同步上述决定。
提案 §2 的 `202b92421571361e4c41edeb4a7ccff7dec09d53` 是历史核查基线，
不能代替实施开始时的 HEAD、工作树和当前契约检查。

当前 owning contracts 为 [Temporal semantics](temporal-semantics.md)、
[Analysis](analysis/README.md)、[Semantic](semantic/overview.md) 和
[source execution](datasource/sources-and-execution.md)。提案描述目标合同；
其中 D01 将改变当前 backend-native membership，不得写成已实现行为。

交付要求：

1. CivilDate、LocalDateTime、Instant 分类、source authority 和 transport 正确；
   保留 Session 默认冻结、reader 默认解析和真实类型/精度错误。
2. 对具备提案所指 raw lineage 的 LocalDateTime、D03 有序时间编码和 D10 组合，
   常量边界过滤、分桶、窗口、Population、版本选择共同使用 §8 wall-order 规则。
   这是必需的语义切换；不能用保留 native 条件的 B/C 替代。
3. 对其他合法表达式维持 A/B/C 路由：A 证明精确等价，B 保留 exact native 条件，
   C 正常执行。无 inverse 不产生新的“不支持分析”错误。
4. `hour_prefix(prefix)` 使用 prefix 的已解析时区上下文，保持现有签名。
   CivilDate 本身不新增 timezone；组合所需上下文沿 prefix 的 reader/default 路径解析。
5. 独立数值 oracle、完整行域和 SQL 证据分别成立；实现、Help、当前文档及 EN/ZH 示例同步。
   本地、真实 provider、物理 pruning、冷恢复和发布证据分别记录。

不增加 per-row timezone、混合 offset 文本 parser、动态 temporal join、通用 inverse registry、
私有 transition 目录、生产 EXPLAIN、数据声明审计、strict mode 或 public predicate-plan 类型。
不重构整个 provider metadata，也不为过滤计划持久化 lineage 或默认升级 Store。
不新增日期枚举或 horizon 限制，不把 DATE 总量分摊到未知日内事件。

## 2. 任务、所有权与依赖账本

Owner 表示责任区域；实际 agent、模型、审查轮次及执行证据统一记录在实施账本。
每项开始前补充局部方案和验收选择，结束时记录文件、命令、结果、未解决项和证据路径。
共享文件按依赖顺序串行整合，不同时修改 `source_time.py` 或 `query_preparation.py`。

| ID | Owner / 主要文件（仓库相对路径） | 交付物及退出条件 | 依赖 | 当前状态 |
| --- | --- | --- | --- | --- |
| T0 | 负责人；本计划与提案 | 记录最新基线、D01–D11 范围和 prefix 继承决定；实施时重核差异 | 无 | 已复核；实施基线见账本 |
| T1 | source authority；`marivo/datasource/engines/{clickhouse,mysql}.py`、`marivo/datasource/timezone.py`、`marivo/analysis/core/time_authority.py`、`compiler/source_time.py` | 补足具体 native kind/transport 缺口；default 和精度合同不漂移 | T0 | 已开发；审查/验收以账本为准 |
| T2 | temporal compiler；`marivo/_timezones.py`、`compiler/source_time.py`、`materialization/time_filter.py`、`temporal_sql.py` | 共享 wall-order 边界翻译、typed constants、比较与 nullable 规则；独立 oracle 通过 | T1 authority 合同 | 已开发；审查/验收以账本为准 |
| T3 | composite source；`marivo/semantic/validator.py`、`compiler/source_time.py`、`materialization/query_preparation.py` | civil-date prefix 验证、prefix 时区继承及 raw lineage；D03/D10 编码复用 T2 | T1、T2 | 已开发；审查/验收以账本为准 |
| T4 | observation / Population；`compiler/observation.py`、`materialization/{query_preparation,graph_source_execution,population_condition}.py` | 过滤与 bucket CASE 共用精确边界；有限/稀疏 cell、窗口和独立 membership 行域一致 | T2、T3 | 已开发；审查/验收以账本为准 |
| T5 | version / sampling；`compiler/member_version.py`、`binding/sampling.py` 及实际 coverage consumer | 版本端点、before-end、open ends 和 sample coverage 与新 membership 一致 | T2–T4 | 已开发；审查/验收以账本为准 |
| T6 | physical optimization；`materialization/time_filter.py`、`temporal_sql.py` 及 T2/T3 helper | 核查 Instant/CivilDate、透明 alias、UTC cancellation 的实际 A 覆盖；B/C 回退不退化 | T2–T5 | 已开发；审查/验收以账本为准 |
| T7 | disclosure；`marivo/semantic/_authoring_values.py`、`semantic/_capabilities/registry.py`、受影响 Analysis Help、docs/site/tests | 对齐 default、prefix 继承、D01 选择与 native projection 的区别；Help 可达性与预算通过 | T1–T6 最终行为 | 已开发；审查/验收以账本为准 |
| T8 | 集成验收；相关 test owners 与本账本 | 冻结集成树，关闭 P2+，完成本地门禁和相关 provider 验证，记录未覆盖范围 | T1–T7 | 已开发；审查/验收以账本为准 |

T2 可以先实现纯 helper 和单元验证；T3–T5 完成前不得对外宣布 D01 完成。
优化覆盖允许分项报告，D01 涉及的同一字段各 consumer 不能交付不同的 selection 语义。
T6 不要求为了获得 A 而反演任意表达式，也不要求增加没有具体收益证据的新 B guard。

## 3. 各任务实施约束

### T1：源类型、解析与传输

- 基于真实物理类型区分 Instant/local clock；补足 ClickHouse 隐式 zone 的
  DateTime/DateTime64 分类，保留原始 precision，不因 Ibis timezone 缺失重新 localize epoch。
  metadata 只扩展实际缺失的事实，走现有 adapter/profile 路径。
- 核实 ClickHouse `native_parse` 与 date prefix 在应用 source zone 前的 UTC 解析语义，
  覆盖非 UTC server/session 上下文，避免二次解释。
- 保留 MySQL TIMESTAMP 的 UTC transport 检查；DATETIME 的业务时区独立解析。
  使用现有连接/时区验证路径确认实际投影需要的 named source zone 可用，
  不把 `CONVERT_TZ` 返回 NULL 当作成功；不新增通用查询前探针框架。
- 保留显式 parser、reader engine/system fallback 的当前优先级；支持的 probe 失败仍失败。
  aware native 输入不得被 parser 时区重新定位；CivilDate 不获得 read timezone。
- 未知 inverse 和真实 precision/provider 限制使用不同处理路径；不通过截断纳秒换取 A。

### T2：一次翻译、多处消费

- 复用 `parse_timezone` / `time_zone` 及现有 source preparation，不引入第二套 timezone policy。
  原始列、类型/precision、source authority 和确切边界由调用者提供；不读取业务行。
- 按提案 §8 实现 `g(b)`：普通 instant 转 source wall clock；fold 第一段取 repeated range
  起点；gap transition instant 取 skipped range 起点。算法不得假设所有跳变都是一小时，
  不设年份或窗口长度 cutoff，不枚举整个查询 horizon。
- 先得到 semantic wall bound，再按 raw precision/encoding 做 ceiling。半开区间和
  `all_history` 缺失端点直接表达；字符串、整数、date、naive timestamp、UTC instant
  常量各匹配 raw 类型，不能让隐式 cast 落到列侧。
- `>=`、`<`、`>`、`<=` 和 equality 分别按语义验证。`g` 描述的是非严格下界；
  gap 上的 `I(w)` 存在平台，不能把所有严格比较机械实现为 `next(g(b))`。
  在进入 version consumer 前，用独立 oracle 固定 transition 上 strict/equality 的正确集合，
  保留 symbolic before-end，不用减一个 timestamp tick 代替。
- 处理物理域上下限和不可表示常量，不制造溢出 sentinel。空范围仍需保留 nullable
  boolean 的真值：WHERE 下丢行等价不等于 NOT/OR 或投影时的三值等价。
- 沿已有 Ibis transparent projection 追 raw lineage；cast alias、coalesce、算术和任意
  parser 不自动获得 exact inverse。native projected value 保持 backend 解释。

### T3：有序编码与 prefix 继承

- D03 使用现有 `_ORDERED_FORMATS`，不收紧合法 `strptime` 语言。
  固定宽度 string 及可排序 compact integer 使用对应 typed bounds；不对 raw integer
  做列侧 string cast 后声称 A。非有序格式继续 native path。
- `hour_prefix(prefix)` 只继承 prefix 的已解析 timezone，不新增 constructor 参数、
  `HourPrefixParse.timezone` 或第二份用户配置。捕获/绑定仍沿用当前 native symbol 合同。
- prefix 必须是 CivilDate；声明阶段检查已知种类，依赖真实 schema 的检查留在 preparation。
  保留 prefix 原始列、date-only parser 和组合所需 authority，避免只传已转换值。
  当前 `SourceTimeAuthority(kind="civil_date")` 明确禁止 `read_timezone`：应从 prefix 的
  source context 解析组合时区，不能往日期 authority 填时区，也不能把 `boundary_timezone`
  当作继承的 source timezone。没有内在时区的 prefix 沿既有 reader/default 路径解析并冻结。
- D10 的 `(date, hour)` 比较先复用 T2，再量化到整小时、处理 hour 24 跨日。
  raw prefix 可为 native DATE 或 D03 date-only encoding；hour 可为 integer 或规范两位 string。
  无可用 raw lineage 时保留合法 native composition；无源数据域审计。
- 共享这一继承关系给 observation、Population 和 version preparation，防止各自重选默认。

### T4 / T5：完整 consumer 切换

- `selected_time`、贡献窗口 predicate、`graph_source_execution` 和 `population_condition`
  的 bucket CASE 使用同一 raw bounds；不能 A 过滤之后仍靠 converted value 分桶。
  一月加三月保持精确 OR；coarse envelope 只能作额外 guard。
- 每个实际 cell/window 独立翻译 clipped endpoints；保留有限 calendar facts、顺序、labels、
  23/25 小时日及 repeated-hour 的两个 instant coordinates。D01 下第一 occurrence 可为空，
  不能删掉该 cell 或把其坐标改成第二 occurrence。
- Population membership、Metric contribution、关系目标时间、输出域保持独立。
  验证 complete keys、outer/empty rows、null、multiplicity、关系 uniqueness 与 fanout；
  不把一个域的时间过滤提前施加到另一个域，不跳过 owning validation。
- snapshot/validity 的每个端点各自解析 authority；保持 open-end NULL/sentinel、
  严格/非严格操作符、symbolic before-end 以及 version cardinality。
  DATE contribution 的完整午夜限制不泛化到 DATE version。
- sampling 对 raw local clock 按可表示 wall-clock 点和同一 membership 计算预期覆盖，
  不为 fold 第一 occurrence 或 skipped clocks 产生虚假的 missing coverage。
  追踪 coverage 的实际比较 carrier，确保 native 投影与 wall membership 差异不会造成
  假缺失；真正缺少的可表示样本仍应失败。Instant/no-lineage 路径保留其现有语义。
  `all_history` 可执行不等于取消 sample coverage 原有的有限 period 要求。

### T6 / T7：优化证据与公开披露

- `cancel_utc`、`optimize_time`、`lower_temporal` 的最终 SQL 是 A/B/C 判定依据。
  raw lineage 的 D01 predicate 不得再次附加会剔除 gap 行的 native exact 条件。
- Instant/CivilDate 及可消除的 transparent/UTC expressions 优先使用现有 helper；
  无证明时保留 native filter，不把一般优化遗漏列为不支持分析。
- 同步当前 temporal contract 中“所有 source membership 由 native conversion 决定”以及
  “exact native predicate 始终保留”等与 D01 冲突的描述；保留 native projection 的事实。
- 对齐 `semantic.time_dimension.from_column`、`semantic.timestamp`、`semantic.strptime`、
  `semantic.hour_prefix` 与相关 Analysis Help、docstrings、实际错误及测试。
  `hour_prefix` 示例仍是 `ms.hour_prefix(prefix)`，不用新增参数演示继承。
- 更新受影响的 `site/src/content/docs/docs/latest/` 与
  `site/src/content/docs/zh-cn/docs/latest/` 示例；不恢复 finalize 或旧 Ref authoring。
  检查 exports/Help/reachability/budgets，只有实际公共集合变化才更新 export snapshot。
- 本轮不修改 AGENTS.md 或 packaged skills。后续若确有指导冲突，记录准确文件/条款，
  按仓库规则单独取得编辑授权；未完成必要对齐时不能宣布完整交付。

## 4. 验收矩阵

优先扩展现有 `tests/analysis/temporal/`、`tests/semantic/`、`tests/datasource/` 与
`tests/conftest.py` / `tests/shared_fixtures.py`；已有 `encoded_time_fixtures.py` 可复用。
新增测试应验证合同或独立结果，不能复刻 helper 算法当 oracle。

| 编号 | 场景 / 观察值 | 独立预期与退出条件 | 责任任务 |
| --- | --- | --- | --- |
| A01 | native DATE/local/aware、显式 parser、reader fallback、Session zone 不同 | 类型和 authority origin 正确；改 report zone 不改 source meaning；probe 失败不 fallback | T1 |
| A02 | NY 2026 fold/gap、Shanghai 历史 transition、非一小时 transition、固定 offset | 对有限 raw clocks 按 `I(w)` 独立枚举；逐边界比较成员集合，覆盖 transition 本身和内部 | T2 |
| A03 | null、精确端点、fractional bound、物理域端点、strict/equality | 完整 nullable 真值；NOT/OR/投影不把 UNKNOWN 改 False；无截断或溢出 | T2、T5 |
| A04 | native Instant/CivilDate 的 A、B、C | 同 backend 独立 native predicate 绕过待测 rewrite；值、null 和精度相同 | T1、T6 |
| A05 | D03 八种有序格式、string/integer、不同 source/report zones | 独立编码点集合与 raw predicate 相同；匹配列类型，量化发生在翻译之后 | T3 |
| A06 | prefix timezone 继承、reader 默认、native/encoded date prefix、integer/string hour | 签名不变，组合 authority 跟随 prefix；跨午夜/transition 的 lexicographic 集合准确；非法 prefix kind 正确失败 | T3 |
| A07 | observation 与等价 Population 时间 predicate | complete keys、values、null、presence、row domain 一致；独立 membership 不被 contribution window 污染 | T4 |
| A08 | 稀疏 calendars、clipped cells、rolling/cumulative/one-sided windows | cell 并集等于 selection；非重叠 calendar 中每行恰好一格；重叠窗口按其自身集合验收；empty fold cell 保留 | T4 |
| A09 | snapshot/validity、各端点 authority、NULL/sentinel、before-end | 独立版本选择与 exact endpoints 相同；不减 tick，不引入重复版本或丢失成员 | T5 |
| A10 | 声明 sample_interval 的 raw local/Instant | fold/gap 不假报 missing；删除一个真正可表示样本会报缺失；coverage 与实际 carrier 一致 | T5 |
| A11 | `%d/%m/%Y`、合法 coalesce/custom expressions、无 raw lineage | 合法 native path 继续执行；优化 miss 不变成错误；真实 DSL/type/provider 限制仍失败 | T3、T6 |
| A12 | 最终 SQL/lineage、参数/常量类型 | A 列侧无转换或隐式 coercion；B 保留 exact 条件；C 可执行；不同 predicate 分别定级 | T6 |
| A13 | ClickHouse implicit-zone/非 UTC parsing、MySQL UTC transport/zone 可用性、Trino connector | 实际 provider fixture 给出值与 SQL 证据；不可用则明确 blocked/unrun，不由编译或历史探针代替 | T1、T8 |
| A14 | Help/当前 docs/EN-ZH examples、必要 frozen facts | 披露无冲突；仅变更 consumed persisted facts 时增加针对性 cold recovery，不默认扩展 Store 工作 | T7、T8 |

A02 至少固定提案中的四个 NY 区间，包含 `[05:00Z,06:00Z)` 空 fold cell 和
`[07:00Z,08:00Z)` 纳入 gap clocks 的行为。允许 native projected value 与所属 cell
在 gap/fold 上不同；该差异不能用来否定 wall-order oracle，也不能遮蔽分桶不一致。

SQL 裸列条件只证明 predicate 形态。只有实际 Runtime EXPLAIN/扫描证据支持的具体
backend、表和 connector 才能声明 pruning/index 收益；不建立 backend × consumer 全排列。

## 5. 执行与审查门禁

以下是未来实施命令，不是本轮已执行结果。先选实际修改范围的最小检查；
`runtime` 以 pytest marker 为准，文件名包含 runtime 不代表真实远端 provider 已验收。

```sh
make test TESTS='tests/analysis/temporal/test_source_time_authority.py tests/analysis/temporal/test_time_filter_rewrite.py tests/analysis/temporal/test_encoded_time_predicate.py'
make test TESTS='tests/analysis/temporal/test_timezone_observation.py tests/analysis/temporal/test_encoded_time_runtime.py tests/analysis/temporal/test_analysis_windows_grain.py'
make test TESTS='tests/semantic/test_semantic_live_help.py tests/semantic/test_semantic_help_examples.py'
make typecheck TYPECHECK_TARGETS='marivo/analysis marivo/semantic marivo/datasource'
make lint-agent LINT_TARGETS='marivo/analysis marivo/semantic marivo/datasource tests/analysis/temporal'
make check-agent
```

按实际 diff 将共享 timezone helper、其他修改的 Python 模块和测试纳入 typing/lint；
以上 focused 范围不覆盖全部 A01–A14，version、sampling、adapter 和 boolean 场景应补入
实际对应节点。最终 `make check-agent` 不带 narrowing variables，其 default tests
承担共享行为的广泛本地回归；通过后只因新改动、失败或未决风险重复相关检查。
真实 provider 测试采用 `make runtime-test TESTS='<已确认的相关 runtime 节点>'`，
在 T8 填入实际选择，不能用空泛命令作为证据。普通实施不运行 release-check 或启动 MinIO。

每任务保留开发自检与正式审查记录，正式审查最多两轮；关闭 P2+ 后进入集成验收。
第二轮仍有 P2+ 时记录具体 blocker 和修复证据，不以“已两轮”判为通过，也不追加第三轮。
实现者自检不冒充独立审查；实际人员与独立性在开始执行时登记。

T8 冻结 source tree 后核对 A01–A14、共享门禁及受影响 provider 结果。
provider 不可用时可报告本地完成，但不得写“provider 验收完成”。
验收通过后依 agent-team-development 创建本地归因提交；不推送、不发布。

## 6. 决策与证据记录

| 事项 | 结论 / 本轮证据 | 后续要求 |
| --- | --- | --- |
| D01 与现有合同差异 | 当前 `temporal-semantics.md` 仍由 native membership + guards 主导；提案要求 raw local wall-order | T2–T5 联动，T7 同步当前合同 |
| prefix timezone | 用户明确要求继承 prefix；取消提案中的可选 timezone 参数 | T3 核查 source context 的传递，不给 CivilDate 加 read timezone |
| parser Help 默认 | 实际 Live Help 仍称 naive native 必须显式带 timezone parser；`source_time` 保留 reader 默认 | T1/T7 对齐实际政策 |
| 编码、分桶、sampling | 已读 `source_time`、`query_preparation`、`member_version`、`binding/sampling`；现有 seams 与计划对应 | 不能据此声称实现完整或数值通过 |
| Runtime 和临时研究 | 提案历史探针属于历史证据；本轮未运行 provider 测试或重放 `/tmp` 文件 | T8 使用当前集成树重新形成相关证据 |

实施时在下表追加实际记录；不提前填写通过或用计划命令代替执行记录。

| 任务/验收项 | 执行者 / 审查者 | HEAD 与工作树范围 | 命令与证据路径 | 结果 / 未决项 |
| --- | --- | --- | --- | --- |
| 本轮文档 | 主 agent；未启动独立实现审查 | 本计划及提案的 prefix 决策同步 | 相对链接、代码围栏、命令路径与 diff 检查 | 仅文档验证；实现未开始 |
