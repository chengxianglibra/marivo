# Marivo 全量分析代数与 Analysis DSL：R10 实施文档

Historical execution records, qualification inventories and one-time validation
scripts were removed during the 2026-10-06 cleanup. Committed records remain in
Git history; local-only execution files were discarded. Recorded phase results
below describe their original scope.

Date: 2026-10-06
Revised: 2026-10-07

Status: R10.1 scoped implementation and validation complete for `642271bf35`;
incremental closure for the current candidate is pending. R10.2-R10.5 remain pending.
Historical R10.1 evidence is located in §1.2. This revision does not establish
installed-package, real-Agent or release acceptance for the current candidate.

## 1. 目标与前置交接

依据[总实施计划](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)
的 R10、§10–§13，完成全库公共契约收口、同一候选安装包验证、完整用户旅程、真实 Agent
及最终交付审计。本文采用五个较粗的工作包，每包给出主要范围、交付物和出口；具体修改
文件、测试节点与执行命令在实施时按实际缺口确定，不预拆成逐函数任务。

用户确认 R0–R9 任务完成，以此启动 R10 规划。文档交付时基线为 `panda`，HEAD 为
`f944ebad394462391eeb4494c4b929e53903ab67`，已包含 R9.7 审计与 R10 交接提交。
本文初次交付仅新增实施文档；实施记录另行绑定实际候选 SHA、未提交 diff、新增文件摘要及依赖版本，
不把 HEAD 单独当作完整候选身份。

前置证据读取 §1.2 定位的 R9.7 审计、R10 交接与历史验收主记录。R9 的历史交接保留
394 个原始 ID：379 个有限 owner-proof 绑定、15 个 `authorized_skipped` 成本出口，
以及 V17 内四个未验证物理 producer 绑定。这些是交接状态，不能转换成 394 项实测通过，
也不授予安装包、真实 Agent 或完整成本资格。

R10 默认不重启用户已明确跳过的成本采集，不机械展开 9,109 个历史种子或旧阶段台账。
获准跳过仅调整已接受的执行范围，原始证明义务及缺口仍保留。最终必需门禁若依赖这些
缺口，登记为未满足；补测或进一步范围修订另作明确决定，不能借“任务完成”改写历史资格。

### 1.1 文档权威与范围

| 权威 | R10 消费的责任 |
| --- | --- |
| [总实施计划](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md) | C01–C18、A01–A13、方法验收义务与完整完成条件 |
| [R0 capability ledger](2026-09-26-marivo-full-refactor-r0-capability-ledger.md)、仍保留的阶段 ledger 与 §1.2 历史索引 | 能力去向、已接受的删除及范围修订；历史行用于追溯，现行契约修订由 owning spec 决定 |
| [Python Analysis design](../../specs/analysis/python-analysis-design.md)、[Operators](../../specs/analysis/operators-and-frames.md)、[Session/Runtime](../../specs/analysis/session-state-and-runtime.md) | 精确类型、方法语义、Cell/域/parts/K、身份、执行与恢复 |
| [Semantic overview](../../specs/semantic/overview.md)与[Datasource layer](../../specs/semantic/datasource-layer.md) | 业务声明、读取治理、凭据、真实物理资格及终端边界 |
| [公共披露规范](../../specs/agent-friendly-public-surface.md)与[AGENTS.md](../../../AGENTS.md) | Help、结果、错误、CLI、文档与 skills 的责任和编辑要求 |
| [R9 实施文档](2026-10-04-marivo-full-algebra-dsl-r9-implementation-plan.md)、§1.2 的 R9.5 SQL ledger 与 R9.7/R10 交接 | 有限代表场景、实际证据、成本缺口与已批准 SQL 边界；按当前方法契约重新判断证据适用性 |
| [测试分工与入口](../../testing/runtime-coverage.md) | 按行为归属复用测试；区分 daily、Runtime、release 和显式多源安装门禁 |
| [marivo-release](../../../.agents/skills/marivo-release/SKILL.md)、当前 [Makefile](../../../Makefile)与[发布 CI](../../../.github/workflows/release.yml) | 发布准备、构建、安装与发布操作的门禁和授权边界 |

本文只安排已有目标的闭合，不新增分析能力、自动 planner、第七种后端、兼容 alias、旧状态
迁移或另一套 registry/Runtime。发现产品缺口时修复其现有 owner，并重验受影响范围；
需要新契约的事项先回到 owning spec 接受，不用临时 wrapper 隐藏问题。

### 1.2 历史证据与当前验收索引

清理前可读取版本为 `642271bf351c686a16b6d45151d4e896bb20c972`，即
`ccbed62962` 的父提交。下列路径均相对于仓库根，读取形式为
`git show 642271bf351c686a16b6d45151d4e896bb20c972:<path>`：

| 历史记录 | path |
| --- | --- |
| R9.5 SQL owner | `docs/superpowers/specs/2026-10-06-marivo-r95-sql-ledger.md` |
| R9.7 审计 | `docs/superpowers/specs/2026-10-06-marivo-r97-completion-audit.md` |
| R10 交接 | `docs/superpowers/specs/2026-10-06-marivo-r10-handoff.md` |
| R10.1 验证 | `docs/superpowers/specs/2026-10-06-marivo-r101-validation.md` |
| 原主验收 | `docs/superpowers/specs/2026-09-26-marivo-full-refactor-acceptance.md` |

这些记录只证明原候选与原范围；引用的附件须逐项确认可取得。已丢弃的本地文件登记为
不可取得，不因文字记录仍在就视为可复核证据；影响必需出口时登记缺口。

R10 实施时创建并维护新的精简索引
`docs/superpowers/specs/2026-10-07-marivo-r10-acceptance-index.md`，本次计划修订不创建
验收结果。索引按 C01–C18、A01–A13 与独立风险关联当前 owner、候选、证据位置和状态，
引用历史 `commit:path`，不恢复已删除的阶段清单、临时脚本或全排列台账。首次增量收口时
建立索引，后续各包补充，R10.5 完成审计；历史分母、范围修订和未满足义务保持可追溯。

### 1.3 本次修订基线与增量范围

本次读取的已提交基线为 `panda@e25ba4a19aa8a0d6bd30524719c8daabc2b0a2a9`。
R10.1 原候选的完成状态保留；以下已提交变化需要纳入当前候选的影响分析和增量收口，
不能直接继承旧候选的安装包、后端或 Agent 资格。

| 已提交变化 | R10 的承接要求 |
| --- | --- |
| 图捕获复用、消费者准入扩展、静态观察契约复用及顺序局部绑定 | 以当前消费者规则、公开声明与真实可组合路径更新能力映射；复用未受影响证据，补验新增组合与拒绝边界 |
| fixed 读取解耦（`c73356872c`）、Store 8 本地信任（`94203b7c27`） | 当前为 Store 8、descriptor v3、continuation v4；分开验收已提交读取与新执行校验，退出旧防篡改和 revalidate 要求 |
| native numeric（`94813fe387`） | 按当前方法版本、实际 component/result carrier 和精度契约重绑 oracle；普通 mean/weighted mean/ratio/linear 及其 rollup 使用 implementation contract v5 |
| A1 前提检查、A2 fixed selection、A3 原量归约与 direct-key L8 | 复用各自有限局部证据，补当前安装旅程中的状态、逻辑身份、共享/物化边界和失败原子性；不扩成新后端或全链路成本资格 |
| 渐进 Help（`9933cc3784`）、analysis skill（`329dcc4cc4`）、semantic guidance（`e25ba4a19a`） | 静态 Help、当前状态、错误修复与工作流判断分别归其 owner；安装后发现路径与真实 Agent 能力映射同步更新 |

局部优化证据见 [A1](2026-10-07-analysis-a1-acceptance.md)、
[A2](2026-10-07-analysis-a2-acceptance.md)、[A3](2026-10-07-analysis-a3-acceptance.md)；
Help 字符/页面预算见[上下文验收](../../testing/analysis-help-context.md)。这些记录的范围和
未验证项独立保留，字符/页面减少不等于真实 Agent 效率或成功率提升。

修订时工作区另有未提交的业务数据展示及相关编译/执行改动，包括
`show(n, max_output_bytes)`、同 grid 的 NoTime category 与 timed numeric 的 fixed
rank/table 组合，以及本地展示检查的执行证据绑定。这些只列为候选纳入项，尚未冻结或
由本文验收。纳入时先确定最终 owning spec、diff 与新增文件摘要，再验证预算/省略、
Cell 与精度披露、身份脱敏、无来源读取，以及组合准入、检查归属和失败边界。若未纳入，
候选清单明确排除；后续变化同样按影响更新，不能将当前工作区等同于上述 HEAD。

## 2. 工作包与实施顺序

主顺序为 `R10.1 → R10.2 → R10.3 → R10.4 → R10.5`。独立 oracle、证据索引和 Agent
业务题可提前准备；安装包与 Agent 的正式验收必须绑定已收口的同一候选。每包先列明
发现的缺口，再做最小修复；前序已有成功证据按实际影响复用，不为改写记录重复业务执行。
本轮从 R10.1 增量收口进入，不重做已关闭的旧链退役。先将 §1.3 的变更映射到受影响
能力、证据和缺口，再固定 R10.2 候选；未提交改动不得绕过这一步进入正式包或 Agent 验收。

| 工作包 | 主要交付 | 出口 |
| --- | --- | --- |
| R10.1 全库与公共契约收口 | 能力/退役去向、公共披露及相关工具同步 | 唯一当前 owner；接口、动态 guidance、类型与文档一致 |
| R10.2 同一候选安装包 | wheel/sdist、来源与依赖检查、安装/CLI 验证 | 包内容正确；隔离安装无源码借用及可选依赖串用 |
| R10.3 安装包完整用户旅程 | A01–A13 脚本、独立 oracle、适用后端和固定/冷恢复证据 | 每条旅程有精确覆盖；source/fixed/cold/installed 分别判定 |
| R10.4 独立真实 Agent | 能力簇覆盖、完整轨迹、失败与新会话重验 | Agent 经公共路径独立完成全部公开能力簇 |
| R10.5 最终门禁与交付审计 | 主验收收口、工程/发布准备记录、证据包与交付结论 | 总计划必需条件逐项判定；未满足项与发布状态明确 |

### R10.1 — 全库与公共契约收口

**目标：**确认最终产品只保留当前接受的执行、存储和公共披露契约。

**主要工作：**

- 按 §1.3 对原 R10.1 候选做增量核对，建立 §1.2 当前索引。为每项变更记录沿用、
  被新契约取代或待补验证的证据，按当前 Store、数值、消费者与披露 owner 收口。
- 以能力/迁移台账反查 exports、imports、调用、方法注册、codec/cache、安装包内容和
  实际发现入口；删除仍可达的被替换家族、转发 alias、SQL 模板及仅维护旧实现的测试。
  保留独立 oracle、有效业务反例及历史证据，不按文件名或 `Dataset` 字样批量删除。
- 反查 ontology、evidence、Session 辅助能力、project/config/secrets、doctor/telemetry
  与安装工具。保留当前契约中的非数值能力，核对凭据、主体键与诊断输出的披露边界。
- 将公共具体类型、docstrings、原生 Help registry、渐进导航和预算、repr/show/contract、
  expected/received/repair、CLI bootstrap 与 latest 中英文示例作为一个变更同步。
  独立验证 exports、可达性、漂移、预算、typing 正反例和可运行示例。
- 核对顺序局部绑定的完整 decorator 示例、当前消费者组合与拒绝路径、渐进 Help
  导航、readiness/source-health 的证据边界，以及已批准 skills 的问题驱动流程。
  §1.3 的展示改动若纳入，连同其编译/执行变化一并验证，不只检查渲染快照。
- 核对已批准 provider、MySQL/ClickHouse 自有查询控制、Store SQL 与 `md.raw_sql`
  终端用途。未批准生产 SQL 必须为零；批准通道保持精确用途，不恢复 SQL 文本分类器、
  编译后改写或执行失败后换路线。
- 提前核对 packaged semantic/analysis skills 的流程和判断边界。若需编辑，先按
  AGENTS.md 取得明确授权，再与本包同步完成；没有授权时保留明确待办及受影响出口，
  不把技能编辑要求留到最终验收才发现。skills 不复制 Help 的 API/参数矩阵。

**交付与出口：**形成有限缺口/退役清单及 C01–C18 去向，修复对应 owner；静态反查与
运行入口证据相互补足。受影响披露、类型、CLI、工具、文档测试通过，广域工程门禁通过；
没有双 owner、兼容/迁移残留或误删的有效公共能力。

### R10.2 — 同一候选安装包与依赖隔离

**目标：**建立后续技术旅程和真实 Agent 共同使用的可复核候选包。

**主要工作：**

- 收口并固定候选代码、声明/数据摘要与依赖；通过 `make pypi-build pypi-check` 构建
  wheel/sdist，记录 SHA-256、版本、包内容及二者代码/资源一致性，不借用旧 dist。
- 在仓库外的独立环境做非 editable 安装；验证 `site-packages`、模块来源、
  `direct_url.json`、实际依赖与干净工作目录。加入 poisoned-source `PYTHONPATH`
  检查，确保运行和新进程恢复不会读到 checkout 产品代码。
- 验证基础安装及各所选 backend extra 的导入/执行与缺驱动结构化错误；未选可选依赖
  不得被隐式导入。核对安装脚本、CLI bootstrap、Python Help、packaged skills 及
  运行所需资源在安装后可用。
- 复用 `tests/packaging/test_wheel.py`、`tests/packaging/test_installer.py`、
  `tests/packaging/test_installer_uv.py` 与 `tests/packaging/test_installed_sources.py`。
  按当前测试分工，功能矩阵留在行为 owner；wheel 门禁证明安装边界与代表性公开旅程，
  只补 A01–A13 和当前变更的具体安装组合缺口，不恢复整套开发测试重放。已有门禁
  成功不能直接登记为完整 R10 安装资格。
- 安装测试默认读取 `dist/pypi`；使用 `MARIVO_TEST_WHEEL_DIR` 时将同一已构建候选
  放入隔离目录并记录 hash。测试会对照 checkout 的产品/资源字节，运行时须固定对应
  checkout。`make release-test` 会重新构建；如产物 hash 改变，按新候选规则处理。

**交付与出口：**一个候选包身份及隔离环境清单，包内容、依赖、安装/CLI 和来源检查
全部通过。后续记录引用同一个 wheel hash；任何产品、包资源或依赖变化都产生新候选，
重新构建并重验受影响项，不把旧包 Agent 轨迹授予新包。

### R10.3 — 安装包完整用户旅程与恢复

**目标：**用公开 Python 入口证明总计划 A01–A13 在安装环境中的完整业务组合。

**主要工作：**

- 每条旅程具有正式 datasource/semantic 项目、独立脚本、独立 oracle 与新进程恢复。
  将 R9 交接列出的 source、方法、producer/recovery、SQL 和统计消费者映射到当前
  版本、准入范围及物理形状，再决定证据复用与组合补验。预期值来自独立业务定义和
  当前方法精度契约，不能从产品 registry 或相同算法实现复制。
- 普通 native numeric 按实际输入、保留分量与输出 carrier 验证；接受规范允许的原生
  舍入及 source/fixed 新续算差异，不套用旧的统一误差界、精确有理数收尾或 HALF_EVEN。
  cold 读取必须保留已存 primary，不重算。Duration、时间折叠、归因、当前行统计及
  其他统计方法仍按各自严格契约验收；下游误差界针对表示后的 Cell，不为原始聚合
  误差背书。非有限值、溢出、非法 Cell、缺失必需状态与真实零分母策略保持独立断言，
  不以“原生精度”放宽。独立 oracle 同时记录数学参照、表示类型与允许差异的依据。
- 同时检查数值、完整键域、多重性、四 Cell、空组、定义/单位/时间角色、原始方法状态及
  可执行 K。保留 J1–J4、两种归约差异、L1/L6/L8/L9 等已有独立反例，不只核对最终数字。
- 分别验证来源再次求值、图内共享、fixed-only 续算、exact hit、断源新进程恢复及
  缺失 payload、不可解码输入、跨 Session、混合输入、资源/取消和原子失败。遵守 Store 8
  本地信任边界，不要求已提交内容的字节/hash 防篡改、私有编译对象深度突变检测或
  `session.revalidate`。生产及新续算仍验证其必需输入、方法状态和输出。
- 从新项目产出 Store 8 后执行断源恢复；读取不能依赖原 producer registry/planner，
  不能重查来源、重放历史或重新拟合，Artifact 不进入 DuckDB。Store 7 及更早状态应
  无迁移、无修改地拒绝，保留旧目录并给出使用新项目的修复。读取已存 Findings 不要求
  打开结果 payload；实际值读取/执行缺文件仍须给出可操作错误。
- 将 A1–A3 的独立风险接入受影响旅程：前提检查实际归属、selection/归约融合的共享与
  显式物化边界、完整原量分量与逻辑身份、deadline/取消及失败不部分发布。保留允许的
  浮点重组差异，不能要求所有融合前后结果逐位相同；局部工作量或耗时证据不授予
  整体性能、远程后端或安装包资格，也不重启已跳过的 R9 成本采集。
- 按 R9 有限方法族/关键风险场景补足安装后见证，保留后端、表拓扑、数值/时间形状、
  路线和 producer 身份。六后端技术资格、共享核与特殊物理差异分别绑定，不要求每条
  A 旅程机械重复于所有后端，也不把一个 producer 的恢复授予其他 producer。
- 已选择服务就绪后，使用 `MARIVO_INSTALLED_MULTISOURCE_TEST=1`、
  `MARIVO_INSTALLED_BACKENDS` 及对应 backend opt-in 执行
  `make installed-multisource-test`。Trino 与 ClickHouse 服务阶段串行互斥；
  服务不可用、未选节点和跳过测试单列状态。

**交付与出口：**A01–A13 每条有脚本/oracle/恢复与能力映射，必需成功和拒绝均有实测
证据；安装包证据逐项绑定真实方法与物理键。source、fixed、cold、installed、后端及资源
分别判定，compile、收集测试或 aggregate green 不代授其他维度。

### R10.4 — 独立真实 Agent 验收

**目标：**证明真实 Agent 能发现并正确使用全部公开能力簇，形成可审计的写—跑—读轨迹。

**主要工作：**

- 从 A01–A13 准备业务题和能力覆盖映射；一题可以覆盖多个簇，以覆盖全部公开能力为
  出口，不机械展开模型×题目×数据库。Agent 可使用已资格后端，其他后端由技术矩阵负责。
- 每次使用隔离项目/会话及 R10.2 候选包，提供业务问题、正式声明、安装环境、公开 Help
  起点及候选包中正式交付的 skills；记录实际可见内容，不另给能力到调用的答案映射。
  不给 oracle、预写答案、私有代码或可复用的正确执行脚本。
- 从当前公开面重建能力覆盖映射，覆盖渐进发现、按业务问题选方法、必要的语义声明
  与顺序局部绑定、状态读取、机械续算和具体错误修复。区分静态加载、readiness 与
  source-health 的结论；读数时遵守 native numeric 与保留状态边界。若纳入展示改动，
  验证 Agent 能识别省略/预算、完整结果与来源覆盖的区别，并走实际可用的后续读取入口。
  Help 字符/页面预算测试仍由技术门禁负责，其通过不替代这些真实轨迹。
- 记录实际模型、工具、完整提示、wheel/依赖、session、操作轨迹、结果与评价。评估由
  独立 oracle 及语义检查完成，覆盖公开发现、续算、错误修复和边界判断。
- 正确数字但使用旧入口、手工 pandas 重写必需 DSL、误用时间域或将相关/归因当因果，
  都不能计通过。保留失败轨迹；修复公开 owner 后，在新项目/会话重新验收受影响能力。

**交付与出口：**全部公开能力簇都有独立真实 Agent 覆盖，失败及修复可追溯。脚本成功
不能代替 Agent，轨迹回放不能代替独立运行，单模型结果不推断普遍成功率。

### R10.5 — 最终工程门禁与完整交付审计

**目标：**逐项判定完整重构和发布准备状态，交付可以取得并审阅的证据。

**主要工作：**

- 在 §1.2 指定的当前验收索引中闭合 C01–C18、
  方法义务、A01–A13、旧链退出、SQL owner、公共披露、后端/资源、包和 Agent 维度。
  采用 R9 接受的有限代表场景与后续实际反例，保留历史分母和范围修订依据；按现行
  owning spec 标明已取代的旧契约，不以不再适用的防篡改或数值义务阻塞当前出口。
  这类契约替代不豁免未受影响的历史缺口，也不把旧方法/旧 Store 的资格转授当前候选。
- 最终执行 `make check-agent`、相关 Runtime、API/site、包与安装门禁；发布准备时按
  release 流程执行 `make release-check`。真实多源安装门禁独立补足，不能因
  `release-check` 成功就认为其 opt-in 服务或 Agent 已执行。
- 校对 release 技能、当前 Makefile/CI 与接受的能力范围。起草时 `release-check` 实际
  调用 `check runtime-test release-test`，当前 Makefile/发布 CI 没有独立的
  `object-storage-test`/MinIO 阶段；技能中的历史段落不构成该门禁已执行的证据，也不
  授权恢复已退出的对象连接能力。发布准备说明必须与实际执行入口一致。
- 每项证据绑定候选 SHA/dirty 摘要、owner/spec/依赖、声明/数据摘要、实际命令、预期、
  结果、失败处置及日志 hash。已有证据仅在明确影响分析后复用；代码变化重验受影响项。
- 将小型索引、独立输入和验收结论纳入版本控制；大型日志、wheel、Agent 轨迹等保留
  独立 hash 与实际可取得位置。只有 `/tmp` 或个人忽略目录的附件不足以成为最终唯一
  证据；证据包保留失败，敏感配置与凭据不进入公开记录。

**交付与出口：**一份最终主验收、包/Agent/后端证据索引与交付说明。总计划 §12 的
必需条件全部满足才登记“完整重构完成”；R9 获准跳过和未验证项若仍影响必需出口，
明确给出受限交付与未满足项，不写成完整资格。

## 3. 验证与完成判定

实施中按改变的 owner 先运行窄门禁，涉及共享或公共行为后执行广域门禁；新增/修改测试
遵守 `marivo-test-fixtures`。本文落盘只做文档检查，不运行数据库、安装包、Agent 或发布门禁。

| 检查范围 | 实施入口 |
| --- | --- |
| 受影响 Python/测试 | `make test TESTS='...'`、`make runtime-test TESTS='...'`、`make typecheck TYPECHECK_TARGETS='...'`、`make lint-agent LINT_TARGETS='...'` |
| 广域工程与 API | `make check-agent`；独立 API 构建使用 `make docs-api` |
| 增量风险与门禁分工 | 以 `docs/testing/runtime-coverage.md` 的当前行为 owner 选窄门禁；Runtime 与 release 标记分别执行，文件路径本身不覆盖默认排除规则 |
| latest 中英文文档 | 在 `site/` 执行 `npm run verify:content` 与 `npm run build` |
| 构建与包内容 | `make pypi-build pypi-check`；冻结候选后用 `.venv/bin/pytest -n 0 -m release tests/packaging/test_installer.py tests/packaging/test_installer_uv.py tests/packaging/test_wheel.py` 验证安装，绑定实际 wheel hash |
| 安装后多源 | 服务就绪，设置 `MARIVO_INSTALLED_MULTISOURCE_TEST=1`、`MARIVO_INSTALLED_BACKENDS` 与所需 backend opt-in，使用同一候选 wheel 执行 `make installed-multisource-test` |
| 最终发布准备 | 按当前 release 流程执行 `make release-check`，另绑定多源安装及真实 Agent |

最终逐项回答：C01–C18 是否闭合；产品是否只剩当前统一链；生产读取是否全部属于 Ibis
或精确批准通道；必需语义/状态/恢复/资源与后端资格是否满足；披露、skills、CLI 与中英文
示例是否一致；同一候选包的技术旅程及真实 Agent 是否通过；成本与扩展记录是否可定位；
所有必需证据是否可取得。任何必需维度未验证、阻塞或失败，保持相应出口未完成。

“重构完成”“发布验证完成”“已发布”分别记录。本文及执行 R10 验收不自动授权版本选择、
tag、push、PyPI/GitHub Release、站点发布或群公告；后续发布按明确请求与 release 技能
执行。当前文档交付也不修改 AGENTS.md 或 packaged skills。
