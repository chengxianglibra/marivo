# R0：全量分析代数与 Analysis DSL 重构的契约冻结实施文档

Date: 2026-09-26

Status: R0 execution plan；本文记录执行顺序和验收口径，R0 台账、必需契约修订与验收尚未完成。

本工作包执行[全量重构实施计划 §9 R0](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md#r0--冻结重构范围契约和可复核基线)。
它为 R1/R2 冻结目标、owner 和可复核的起点；不修改产品代码，不把历史 MVP 或旧后端资格
写成新 DSL 的通过记录。本文只规划 R0，R1–R10 的实现和最终验收仍受主计划约束。

## 1. 输入、权威与当前快照

契约接受顺序沿用主计划 §1.2：本轮五项原则、[代数 v0.5](2026-09-23-analysis-algebra-theory.md)、
[接口设计](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md)、
[架构设计](2026-09-24-marivo-analysis-dsl-architecture-design.md)，然后是经本轮修订接受的
`docs/specs/` owning specs。[MVP 验证计划](2026-09-24-marivo-analysis-dsl-mvp-validation-plan.md)、
当前代码/Help/测试和旧验收用于定位现状与反例，不自动决定目标语义。

编写本文时 `panda` 的 HEAD 为 `95cb4ecff8126362a409246acebc2b223966a3d3`。
四份设计输入的 SHA-256 与主计划 §1.1 相符。当前工作树已有未跟踪的主计划文件；
R0 执行者须先记录实际 HEAD、分支、`git status --short`、输入 hash 与未提交 diff，
再建立隔离的 `codex/` 工作分支或工作树。不得清理、移动或覆盖本工作树的未跟踪计划。
每次接续若 HEAD 或输入 hash 改变，重新核对受影响的台账单元，不能沿用本段快照冒充现状。

旧组合验收记录 `docs/superpowers/plans/2026-09-24-marivo-analysis-dsl-composition-acceptance.md` 在本机可读，
但 `docs/superpowers/plans/` 被 `.gitignore` 忽略。它只提供待归档的历史事实与反例。
旧 C0–C10 受版本控制的验收记录在 `docs/superpowers/specs/`；各记录只证明其注明的
代码、输入、后端和运行环境。R0 需要索引其可取得性，不在新 checkout 假设忽略目录存在。

## 2. R0 产物及唯一记录位置

| 产物 | 拟定文件 | 本阶段必须填实的内容 |
| --- | --- | --- |
| 能力与支持矩阵 | `2026-09-26-marivo-full-refactor-r0-capability-ledger.md` | C01–C18、方法子单元、旧/目标入口、K、类型/后端/路线、反例、阶段与 owner |
| 必需契约决定及规则映射 | 对应 `docs/specs/` owning spec；索引在能力台账 | 具体 typed 输入、量/域/Cell/部件、前提、拒绝、K、规则版本；一项事实只在一个 owning spec 定义 |
| SQL 与适配替代清单 | `2026-09-26-marivo-full-refactor-r0-sql-ledger.md` | 每个生产 SQL 构造/提交点、控制语句、Ibis 补丁及 Artifact→DuckDB 路线的调用方、用途、目标 owner 和处置 |
| 破坏性变更清单 | 能力台账的独立章节 | 旧导出、Help、方法/协议身份、SQL/parity、Store/Artifact 的删除或收紧及消费者 |
| 历史证据索引 | `2026-09-26-marivo-full-refactor-r0-evidence-index.md` | hash、代码 SHA、依赖/环境、路径可取得性、独立预期来源、可复跑命令与证据边界 |
| 阶段验收主记录 | `2026-09-26-marivo-full-refactor-acceptance.md` | R0 每项出口的通过/失败/未验证/阻塞；后续 R1–R10 在同一文件续记 |

以上文件名相对 `docs/superpowers/specs/`。能力台账可以有受版本控制的机器可读伴随索引，
但 Markdown 是审阅入口，机器文件不能另定语义。大日志或 wheel 放在可取得的受控证据包，
索引记录 hash 与位置；仅有 `/tmp` 或 Git 忽略目录路径的附件不能成为最终唯一证据。
R0 验收文件只记实际完成事项，不预填“通过”。

## 3. 实施顺序

### R0.1 固定工作基线和历史证据边界

1. 在隔离工作树记录 HEAD、分支、未提交路径及 diff hash；保存四份设计输入和主计划的 hash。
   对未跟踪主计划先确保隔离工作树可读同一内容，不因 `git worktree add` 未复制未跟踪文件而
   在新工作树引用空文件。记录隔离工作树起点，后续每阶段另记代码 SHA 与 diff hash。
2. 逐份登记 MVP J1–J4、C0–C10、Runtime、安装 wheel 和真实 Agent 记录：事实是什么，
   原始日志/轨迹能否取得，使用的代码与依赖是什么。缺原始附件时标“历史记录，待重跑”；
   旧脚本、mock、`/health`、编译或静态读取不代替真实执行、安装包或 Agent 证据。
3. 把可复用的手算输入、SQL/Fraction/平均秩等**独立** oracle、故障注入和业务问题与
   旧执行产物分开。候选 reducer 的输出不得写回预期值；失败轨迹原样保存。

出口是可在另一个 checkout 按索引定位的输入和历史边界；本步不宣称新资格通过。

### R0.2 反查当前公开面、消费者与能力去向

以 `marivo/{datasource,semantic,analysis}/__init__.py`、
`marivo/analysis/public_dsl.py`、`marivo/analysis/session/`、
`marivo/analysis/observation/`、`marivo/analysis/datasets/`、三层 `_capabilities/`、
`tests/test_public_surface.py` 和 `tests/test_agent_api_drift.py` 为第一批索引，
再由调用方/import/Help target 反查。代码中可见 `J1Context`、`J3Observed`、
`DatasetRuntime.execute_j1`、旧 Dataset/Population 入口；这些是迁移清单的线索，
不是预先认定每个文件都要整份删除。

能力台账先列 C01–C18，再拆到每个可独立准入的方法单元。每单元至少填：

| 字段组 | 必填事实 |
| --- | --- |
| 当前 | 可导入符号、Help target、实际调用链、当前 owner、相关状态/codec、测试和现有后端资格来源；若只在旧文档出现，标为历史残留 |
| 目标 | 唯一公开构造与具体类型、方法/规则版本、Logical 与 Materialized 视图、允许和拒绝的 K、owner 与实施阶段 |
| 资格 | 数值/时间/来源形状、后端及表类型、候选执行路线、必须检查的前提和结果/部件、独立正反例及精确验收命令 |
| 判定 | 保留、收紧或删除的理由；现状证据状态与目标必需状态分别记录，禁止用“尚未实现”改变目标范围 |

扫描按主计划 C01–C18 和 §3 的模块责任表双向进行：从能力找代码，也从导出、Help、
SQL 提交、Store 格式和测试反找能力。C01/C02 的 datasource/semantic authoring、
C03–C10 的关系/数值、C11–C14/C18 的领域方法、C15–C17 的运行/披露/工具都要覆盖；
领域 `Event`/`Lifecycle`、ontology、项目工具和可选依赖不能因不在 MVP 链上而漏记。

### R0.3 接受必需语义差异和未闭合公共契约

先在唯一 owning spec 写入目标规则，再冻结矩阵。实现阶段不得依据本文概述自行猜签名。

| 决定单元 | 应写入的 owner 与必须闭合的问题 | 独立反例 |
| --- | --- | --- |
| C18 Anchor/retention | `docs/specs/analysis/python-analysis-design.md`：具体 typed 构造、Anchor 身份及多锚点域、elapsed/calendar 窗口、重叠贡献政策、覆盖载体、Subject 与 Subject×Anchor 实例单位、K+/K-/K?、固定 Ω、上下界及 K | DST 七日与 168 小时；多锚点重叠；100 人中 25 真/5 假/70 未知给出 [25%,95%]，不得隐式删未知 |
| 权重与参照 | 同一 analysis owner；必要的 semantic 单位/组件规则归 `docs/specs/semantic/semantic-object-model.md`：`StatisticalWeight`、`ReferenceWeights` 的构造与绑定，固定分母、身份/分层对应、缺层、零分母、状态和 K | 订单数冒充统计权重；Top-K 后重算参照；缺层自动归一化 |
| 普通 Relation ratio | analysis owner：两关系的 typed 输入、同域/对应/单位/时间条件、零分母和缺侧 Cell、输出部件与可续算范围；与 `ms.ratio`、`mv.runtime_metric.ratio` 分清 | 数字可除但实例域不对应；缺侧误作零；普通比率误称 share |
| 业务顺序依据 | `docs/specs/semantic/semantic-object-model.md` 定义可声明的顺序/冲突事实，analysis owner 定义 replay/matching 的接受及拒绝；不把 occurrence ID 的稳定排序当成业务先后 | 同时刻 activate/deactivate 对调后终态或保留轨迹改变 |
| 原状态与当前行 | analysis owner：`rollup` 与 `summarize`、count/count_defined、合法空状态、零分母、缺侧、时点版本、distinct/quantile 直接观察及 K 收紧 | 当前行均值与组件总比率不同；Undefined 的合法零组件可合并但不能当零当前值 |
| SQL/parity 公开边界 | `docs/specs/semantic/datasource-layer.md`、semantic parity 与 analysis owner：任意 SQL 执行入口的删除清单、provenance 文本边界及替代 oracle | `md.raw_sql`/`backend.sql` 绕过 Ibis；`ms.from_sql` 文本被误执行 |

每个决定附明确的输入与输出类型、错误结构、状态/部件、验收反例和 `docs/specs/` 文件位置。
若 C18 或其他本轮必需输入仍未闭合，R0 标为阻塞；`evaluate_each`、多对多、跨源混合、
bootstrap/因果等主计划 §6.2 的研究或独立扩展另列范围，不用占位 API 填平缺口。

### R0.4 冻结规则、方法和模块责任

为主计划 §4.2 的六类元算子分别登记输入签名、参数封闭变体、输出、前提、RequiredParts、
PartTransform、后置条件、Transport、Eval 与 rule owner。方法行再记录方法身份/版本、
量/域/Cell/空输入/数值政策、必需检查、K 及独立 oracle。semantic 声明的依据和显式 builder
推导的依据分开，方法语义与物理实现资格分开；Event matcher、Lifecycle replay、归因、
排名、相关和预测保留自身方法身份，不被通用 reduce 替代。

以主计划 §3 的现有模块表为起点，补齐实际 import 与调用图，给每个旧实现和消费者指定
`datasource adapters`、`semantic`、`analysis core/relations/methods/compiler/materialization/session`
之一的唯一目标责任。记录 R1–R10 哪一包同时迁移消费者并删除旧分支；只改名、alias、
转发 shim 或并行 registry 不算完成。R0 固定责任和类型方向，不预建空包或虚构最终类名。

### R0.5 逐一登记 SQL 和适配路线

从 `marivo/datasource/engines/`、`manage.py`、`source_health.py`、semantic parity、
`marivo/analysis/compiler/`、`materialization/`、`session/` 及提交调用点反查。
当前可见的线索包括 `postprocess_sql`、`md.raw_sql`、`SELECT 1` 探测、
`postgres_event_sql.py`/`trino_event_sql.py`、`compiler/lifecycle.py`、
`compiler/driver_numeric.py` 和 Artifact 的 DuckDB 读取路线。`rg` 只能找候选，
每项必须追到运行用途和调用方；Store owner 内的 SQLite 事务另作白名单审计。

SQL 台账的每行记录：文件/符号、调用方、业务读取/元数据/校验/控制/Store 分类、后端、
当前输入与实际提交方式、是否手写/AST 拼接/生成后补丁、目标 Ibis 表达或公开驱动 API、
改造阶段、真实运行的证明方式、阻塞与例外状态。原则是 Ibis 构造所有 datasource 读取与
数据校验表达式；adapter 可以原样提交绑定表达式身份的 Ibis 编译产物。Ibis 准备→Python
须在执行前获准，不是源端失败后的回退。没有等价路径的必需单元标阻塞，**例外默认空**；
具体 SQL 例外只有用户另行明确批准后才可进入矩阵。

从 `EngineProfile`、`ExecutionAdapter`、`BatchStream` 的当前职责归并出一份内部 adapter
接口决定，按主计划 §5.1 七项责任核对六后端：provider/连接、物理来源、实现资格、
Ibis 编译与传输、解码、资源取消、来源覆盖依据。记录所选依赖的延迟导入和单一资格判断 owner。
目标资格矩阵以“方法 × 数值类型 × 时间/来源形状 × 后端/表类型 × 路线”为键，
对 DuckDB、PostgreSQL、MySQL、SQLite、Trino、ClickHouse 均列必需正例、负例与物理差异。
旧 C0–C10 仅选反例；新 DSL 的真实后端资格由 R1/R9 重新取得。

### R0.6 收束破坏性变更、证据与交接

破坏性变更清单至少覆盖旧 Population/Dataset 家族、J1–J4 产品身份、旧 Help target、
旧 registry/codec/协议、definition-only 来源命中、Artifact→DuckDB、任意 SQL/parity、
distinct/quantile 的原量续算、旧 Store 双读/迁移及公开参数收紧。每项给出旧使用处、
目标入口或结构化拒绝、更新阶段及对应 Help/CLI/site/测试消费者；J1–J4 仅保留为验收旅程 ID。

在主验收文件记录 R0 每项实际产物、hash、审阅的 owning spec 版本、未闭合项与阻塞。
R1/R2 交接列出已冻结的目标单元、接口责任、模块消费者、必须保留的独立反例、
首批适配矩阵和禁止继续使用的旧路径。packaged `marivo-semantic`/`marivo-analysis` skill
需要同步时，编辑前遵守仓库 AGENTS.md 的显式用户批准要求；R0 不借交接提前编辑它们。

## 4. 核验与出口

R0 是文档和事实核验包。使用 `rg --files`/`rg -n` 建立导出、调用、SQL、协议扫描记录；
用 `git ls-files`、`git check-ignore -v`、`shasum -a 256` 验证证据是否受版本控制且内容稳定；
用 `git diff --check` 检查文档。若执行者声称重跑某项基线，必须附精确命令、退出码、环境、
日志与独立预期；不得从本文推断测试已经运行。R0 不以远端数据库重跑或完整
`make release-check` 作为必经门禁，也不将早期 wheel/Agent 记录升级为当前通过。

R0 只有在以下项目均可从受版本控制的产物追到具体 owner 时才能标通过：

1. C01–C18 每个现有能力有保留、收紧或删除的目标；每个必需方法子单元有目标输入、K、
   阶段、后端/类型/路线单元、独立正反例和验收命令，历史残留与实际代码分开。
2. C18、权重/参照、普通 ratio、业务顺序等必需契约已在唯一 owning spec 接受；零分母、
   缺侧、原状态/当前行、时点版本及 distinct/quantile 收紧无未决定的公共输入。
3. 所有生产 SQL/补丁/控制语句与 Artifact→DuckDB 入口有调用方、用途、目标 owner、
   阶段和处置；无默许 SQL 例外或旧协议迁移任务。
4. 六后端目标矩阵、历史证据可取得性和独立 oracle 可复核；通过、失败、未验证、阻塞
   分列，缺真实环境仍保持未验证或阻塞。
5. 每阶段可从能力行追到规则/模块 owner、消费者、breaking change 和验收索引。

任一出口未满足时，主验收文件标明具体阻塞单元和所需决定，R1/R2 只能推进不依赖该单元的
工作，不能把 R0 整体写成通过。R0 通过只表示范围与契约可实施，不表示重构、后端或 Agent
验收完成。
