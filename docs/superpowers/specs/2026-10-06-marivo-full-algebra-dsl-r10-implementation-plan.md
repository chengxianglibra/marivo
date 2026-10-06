# Marivo 全量分析代数与 Analysis DSL：R10 实施文档

Date: 2026-10-06

Status: R10.1 scoped implementation and validation complete; R10.2-R10.5 pending.
See the [R10.1 validation record](2026-10-06-marivo-r101-validation.md). Installed-package,
real-Agent and release acceptance have not been performed.

## 1. 目标与前置交接

依据[总实施计划](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)
的 R10、§10–§13，完成全库公共契约收口、同一候选安装包验证、完整用户旅程、真实 Agent
及最终交付审计。本文采用五个较粗的工作包，每包给出主要范围、交付物和出口；具体修改
文件、测试节点与执行命令在实施时按实际缺口确定，不预拆成逐函数任务。

用户确认 R0–R9 任务完成，以此启动 R10 规划。文档交付时基线为 `panda`，HEAD 为
`f944ebad394462391eeb4494c4b929e53903ab67`，已包含 R9.7 审计与 R10 交接提交。
本文初次交付仅新增实施文档；实施记录另行绑定实际候选 SHA、未提交 diff、新增文件摘要及依赖版本，
不把 HEAD 单独当作完整候选身份。

前置证据读取[R9.7 审计](2026-10-06-marivo-r97-completion-audit.md)、
[R10 交接](2026-10-06-marivo-r10-handoff.md)与
[验收主记录](2026-09-26-marivo-full-refactor-acceptance.md)。R9 的当前交付保留
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
| [R0 capability ledger](2026-09-26-marivo-full-refactor-r0-capability-ledger.md)与各阶段当前 ledger | 能力去向、已接受的删除及范围修订；历史行用于追溯 |
| [Python Analysis design](../../specs/analysis/python-analysis-design.md)、[Operators](../../specs/analysis/operators-and-frames.md)、[Session/Runtime](../../specs/analysis/session-state-and-runtime.md) | 精确类型、方法语义、Cell/域/parts/K、身份、执行与恢复 |
| [Semantic overview](../../specs/semantic/overview.md)与[Datasource layer](../../specs/semantic/datasource-layer.md) | 业务声明、读取治理、凭据、真实物理资格及终端边界 |
| [公共披露规范](../../specs/agent-friendly-public-surface.md)与[AGENTS.md](../../../AGENTS.md) | Help、结果、错误、CLI、文档与 skills 的责任和编辑要求 |
| [R9 实施文档](2026-10-04-marivo-full-algebra-dsl-r9-implementation-plan.md)、[R9.5 SQL ledger](2026-10-06-marivo-r95-sql-ledger.md)、R9.7/R10 交接 | 有限代表场景、实际证据、成本缺口与已批准 SQL 边界 |
| [marivo-release](../../../.agents/skills/marivo-release/SKILL.md)、当前 [Makefile](../../../Makefile)与[发布 CI](../../../.github/workflows/release.yml) | 发布准备、构建、安装与发布操作的门禁和授权边界 |

本文只安排已有目标的闭合，不新增分析能力、自动 planner、第七种后端、兼容 alias、旧状态
迁移或另一套 registry/Runtime。发现产品缺口时修复其现有 owner，并重验受影响范围；
需要新契约的事项先回到 owning spec 接受，不用临时 wrapper 隐藏问题。

## 2. 工作包与实施顺序

主顺序为 `R10.1 → R10.2 → R10.3 → R10.4 → R10.5`。独立 oracle、证据索引和 Agent
业务题可提前准备；安装包与 Agent 的正式验收必须绑定已收口的同一候选。每包先列明
发现的缺口，再做最小修复；前序已有成功证据按实际影响复用，不为改写记录重复业务执行。

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

- 以能力/迁移台账反查 exports、imports、调用、方法注册、codec/cache、安装包内容和
  实际发现入口；删除仍可达的被替换家族、转发 alias、SQL 模板及仅维护旧实现的测试。
  保留独立 oracle、有效业务反例及历史证据，不按文件名或 `Dataset` 字样批量删除。
- 反查 ontology、evidence、Session 辅助能力、project/config/secrets、doctor/telemetry
  与安装工具。保留当前契约中的非数值能力，核对凭据、主体键与诊断输出的披露边界。
- 将公共具体类型、docstrings、原生 Help registry、渐进导航和预算、repr/show/contract、
  expected/received/repair、CLI bootstrap 与 latest 中英文示例作为一个变更同步。
  独立验证 exports、可达性、漂移、预算、typing 正反例和可运行示例。
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
- 复用并扩展 `tests/test_analysis_runtime_wheel.py`、安装脚本测试和
  `tests/test_installed_multisource.py`。已有 J1–J4/R5–R8 门禁是起点；按 R9 交接补入
  当前适用见证，不能将旧门禁的成功直接登记为完整 R10 安装资格。

**交付与出口：**一个候选包身份及隔离环境清单，包内容、依赖、安装/CLI 和来源检查
全部通过。后续记录引用同一个 wheel hash；任何产品、包资源或依赖变化都产生新候选，
重新构建并重验受影响项，不把旧包 Agent 轨迹授予新包。

### R10.3 — 安装包完整用户旅程与恢复

**目标：**用公开 Python 入口证明总计划 A01–A13 在安装环境中的完整业务组合。

**主要工作：**

- 每条旅程具有正式 datasource/semantic 项目、独立脚本、独立 oracle 与新进程恢复。
  复用 R9 交接列出的 source、方法、producer/recovery、SQL 和统计消费者，补足组合缺口；
  预期值来自独立业务定义，不能从产品 registry 或相同算法实现复制。
- 同时检查数值、完整键域、多重性、四 Cell、空组、定义/单位/时间角色、原始方法状态及
  可执行 K。保留 J1–J4、两种归约差异、L1/L6/L8/L9 等已有独立反例，不只核对最终数字。
- 分别验证来源再次求值、图内共享、fixed-only 续算、exact hit、断源新进程恢复及
  损坏/缺失部件、跨 Session、混合输入、资源/取消和原子失败。恢复不能重查来源、重放
  历史或重新拟合；Artifact 不进入 DuckDB。
- 按 R9 有限方法族/关键风险场景补足安装后见证，保留后端、表拓扑、数值/时间形状、
  路线和 producer 身份。六后端技术资格、共享核与特殊物理差异分别绑定，不要求每条
  A 旅程机械重复于所有后端，也不把一个 producer 的恢复授予其他 producer。
- 已选择服务就绪后，使用 `MARIVO_INSTALLED_MULTISOURCE_TEST=1` 及对应 backend
  opt-in 执行 `make installed-multisource-test`。Trino 与 ClickHouse 服务阶段串行互斥；
  服务不可用、未选节点和跳过测试单列状态。

**交付与出口：**A01–A13 每条有脚本/oracle/恢复与能力映射，必需成功和拒绝均有实测
证据；安装包证据逐项绑定真实方法与物理键。source、fixed、cold、installed、后端及资源
分别判定，compile、收集测试或 aggregate green 不代授其他维度。

### R10.4 — 独立真实 Agent 验收

**目标：**证明真实 Agent 能发现并正确使用全部公开能力簇，形成可审计的写—跑—读轨迹。

**主要工作：**

- 从 A01–A13 准备业务题和能力覆盖映射；一题可以覆盖多个簇，以覆盖全部公开能力为
  出口，不机械展开模型×题目×数据库。Agent 可使用已资格后端，其他后端由技术矩阵负责。
- 每次使用隔离项目/会话及 R10.2 候选包，只提供业务问题、正式声明、安装环境与 Help
  起点。不给 oracle、预写答案、私有代码或可复用的正确执行脚本。
- 记录实际模型、工具、完整提示、wheel/依赖、session、操作轨迹、结果与评价。评估由
  独立 oracle 及语义检查完成，覆盖公开发现、续算、错误修复和边界判断。
- 正确数字但使用旧入口、手工 pandas 重写必需 DSL、误用时间域或将相关/归因当因果，
  都不能计通过。保留失败轨迹；修复公开 owner 后，在新项目/会话重新验收受影响能力。

**交付与出口：**全部公开能力簇都有独立真实 Agent 覆盖，失败及修复可追溯。脚本成功
不能代替 Agent，轨迹回放不能代替独立运行，单模型结果不推断普遍成功率。

### R10.5 — 最终工程门禁与完整交付审计

**目标：**逐项判定完整重构和发布准备状态，交付可以取得并审阅的证据。

**主要工作：**

- 在[验收主记录](2026-09-26-marivo-full-refactor-acceptance.md)中闭合 C01–C18、
  方法义务、A01–A13、旧链退出、SQL owner、公共披露、后端/资源、包和 Agent 维度。
  采用 R9 接受的有限代表场景与后续实际反例，保留历史分母和范围修订依据。
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
| latest 中英文文档 | 在 `site/` 执行 `npm run verify:content` 与 `npm run build` |
| 构建与包内容 | `make pypi-build pypi-check`，隔离安装来源/依赖及公开旅程 |
| 安装后多源 | 服务就绪、所选 opt-in 和同一候选 wheel 下执行 `make installed-multisource-test` |
| 最终发布准备 | 按当前 release 流程执行 `make release-check`，另绑定多源安装及真实 Agent |

最终逐项回答：C01–C18 是否闭合；产品是否只剩当前统一链；生产读取是否全部属于 Ibis
或精确批准通道；必需语义/状态/恢复/资源与后端资格是否满足；披露、skills、CLI 与中英文
示例是否一致；同一候选包的技术旅程及真实 Agent 是否通过；成本与扩展记录是否可定位；
所有必需证据是否可取得。任何必需维度未验证、阻塞或失败，保持相应出口未完成。

“重构完成”“发布验证完成”“已发布”分别记录。本文及执行 R10 验收不自动授权版本选择、
tag、push、PyPI/GitHub Release、站点发布或群公告；后续发布按明确请求与 release 技能
执行。当前文档交付也不修改 AGENTS.md 或 packaged skills。
