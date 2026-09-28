# R4：统一 Runtime、交换与存储，吸收 MVP 实施文档

Date: 2026-09-28

Status: implementation plan；本次仅编写 R4 实施文档，不代表产品实施或验收。

## 1. 依据、起点与完成定义

执行[总实施计划 R4](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)，
重点落实其 §7、§8、§10–§13。理论和语言依据为
[分析代数契约](2026-09-23-analysis-algebra-theory.md)、
[DSL 接口设计](2026-09-24-marivo-semantic-analysis-dsl-interface-design.md)和
[执行架构设计](2026-09-24-marivo-analysis-dsl-architecture-design.md)。
前置交接见 [R3 实施文档](2026-09-27-marivo-full-algebra-dsl-r3-implementation-plan.md)与
[验收主记录](2026-09-26-marivo-full-refactor-acceptance.md)。

用户确认 R0/R1/R2/R3 任务完成，以此启动 R4 规划。起草 checkout 的 HEAD 为
`899eac66b5`，存在 R3 acceptance corrections 对应的未提交代码、测试和文档；本次不修改它们。
验收主记录仍保留历史未通过、64 项 skip 及后续阶段归属，最新 corrections 明确不提升
整个阶段资格。进入实施时记录准确 SHA、未提交 diff/新增文件 digest 和前置交接，逐项核对
实际需要的接口与资格；不重做已交付工作，也不把进度确认替换成未取得的运行证据。

R4 完成意味着：J1–J4 业务旅程通过一个有类型图、一套方法语义注册、一个顶层执行协调、
现有唯一 Store owner 和一套新协议运行；source-only 每次新求值，fixed-only 精确复用，
mixed 在读取和 Run 前拒绝；新产物可以在断源的新进程恢复并执行承诺的 K（保留续算能力）。
产品不再通过场景类型、方法 ID 或场景 codec 分派。这是行为与责任迁移，不是文件改名。

## 2. 范围与 owning specs

R4 承接现有已闭合 J1–J4 的成员、读取、观察、选择、分组、归约、比较、ratio、association
及其固定视图；具体数值、类型、物理形状、时间和后端限制继续按已取得资格执行。
完整成员/时间/多根扩张归 R5，完整比较/参照/归因归 R6，领域方法归 R7/R8，六后端完整
资格与全库 SQL 清零归 R9。统一运行协议不授予这些方法资格。

R4 对已迁入方法删除旧实现和消费者，不保留 alias、双注册、旧执行回退或双读。
尚待 R5–R8 的能力逐项登记 owner、拒绝位置和恢复条件；不得绕过新协议继续写旧 Artifact。
如公共调用必须收缩，先在 owning spec 接受明确的结构化拒绝和披露，再改消费者。
不能以无限扩大 R4 方法范围或隐藏旧入口解决阶段衔接。

实施前先在下列唯一 owner 接受具体契约，再修改产品：

| Owner | R4 必须更新的事实 |
| --- | --- |
| [Python analysis design](../../specs/analysis/python-analysis-design.md) | Logical/Materialized 边界、唯一图消费者、准入和动态 K；替换相冲突的 MVP 当前描述 |
| [Session state and Runtime](../../specs/analysis/session-state-and-runtime.md) | identity、执行顺序、并发、故障协调、新协议、恢复；移除已迁入路线的旧缓存及 Artifact DuckDB 授权 |
| 现有交换、物化、方法 owning specs（R4.1 定位到章节） | BatchStream、主表/parts、检查证据、方法状态 codec 的唯一责任 |
| 验收主记录 | 本阶段逐格结果、代码与协议版本、命令、附件、删除清单及剩余交接 |

本计划不预定实际 schema 版本号或新公共名字。R4.1 必须根据当前 Store schema、descriptor、
receipt、snapshot 与方法状态格式冻结具体版本和闭合字段；未冻结不能进入协议实现。
不以任意字典、可选字段大类、Python 类名或 `Any` 代替封闭的类型变体。

## 3. 执行协议与不可放宽的约束

### 3.1 输入、身份和共享

仅遍历执行根的实际依赖。Materialized 为固定叶子，其历史 lineage 不展开为来源依赖。
跨 Session 输入、独立捕获且不满足共同成员绑定的比较、已知不支持的实现和 mixed 输入，
必须在 source open、Artifact 行读取和 Run 分配前失败。

| 类别 | 身份及执行顺序 | 必需反例 |
| --- | --- | --- |
| source-only | 纯准入 → writer guard/协调 → 分配 Run 与新求值身份 → 固定实现 → 执行和发布；稳定 definition 不能命中旧来源产物 | 同一 Lazy 连续执行，期间改源，返回不同 Run/Artifact 与新数据 |
| fixed-only | 校验精确输入绑定和 receipt，计算 key；验证成功的精确命中返回原 Artifact，无新 Run；未命中才进入受保护执行和发布 | 相同数字但不同输入引用、端点顺序、方法/状态版本、绑定或部件不得误命中 |
| mixed | 静态分类后立即结构化拒绝，不读取任一输入 | 两种叶子顺序都拒绝，I/O 与 Run 计数均为零 |

定义身份、来源求值身份、执行 key、Run ref 与 Artifact ref 分别有唯一 owner。
来源 key 绑定稳定定义、规范计划/方法版本、确切输入与本次求值；固定 key 绑定精确且有序
输入引用、主表/parts receipt、定义/绑定和协议版本。序列化规范、排序和 hash 输入须可测试。
key 命中只在所需完整性校验通过后成立，不能仅靠索引存在或数值相等。

单次执行中的同一个显式节点真实实现一次，所有消费者消费同一绑定。以节点身份判定共享，
不把独立构造的相等定义自动合并。读取/实现计数必须证明复用；重复子查询文本不算共享。
顶层 execute 之间不共享来源实现身份。共享不承诺未经资格验证的跨表/跨源事务快照。

### 3.2 唯一协调与检查

Runtime 消费 R3 GraphPlan、lowering 输出、注册实现与有序检查义务；不按 J1–J4 或 backend
名称选择分支。adapter 负责来源资源和实际提交，本地算法不连接来源、不创建 Run、不发布。
一次顶层执行只分配一个 Run；内部阶段结果不能成为可见半成品。

检查保留来源节点、作用域、输入顺序和绑定。重复符号检查不能丢失独立输入，重复共享节点
也不能把二输入检查扩成四输入。静态证明、待执行检查和已完成证据严格区分；实际检查失败
必须阻止发布，并通过结构化错误给出 expected、received、具体 repair。
实现选择固定后，失败、取消或类型错误不触发换后端、换本地算法或新身份重试。

### 3.3 交换、部件与恢复

复用现有固定 schema、显式 RecordBatch 和可关闭 BatchStream。source、receipt-checked
Parquet 与 pandas→Arrow 生产者履行同一交换契约。空流仍带 schema；四 Cell 标签/原因、
MissingCoordinate、空部件与缺部件保持区分。主表及每个必要 part 独立记录 schema、键、
基数、hash、方法状态版本和绑定；只有全部验证成功才可发布。

nullable 大整数、复合键、Decimal、时间精度/时区与 Duration 按方法资格验证或明确拒绝，
不得经 float 静默失真。批次切分/换序不能改变键关联和方法结果。需要耗尽才能完成的检查，
提前 close 不能记 completed。正常、异常、取消都关闭拥有的 reader、游标和临时资源。

fixed-only 执行使用受控 Arrow/Parquet 读取后进入 pandas；禁止 Artifact→DuckDB/远端。
完整输入算法须明确记录 Arrow/pandas/NumPy 与工作区共存成本；不通过分批接口暗示有界
内存，不自动 spill、采样、截断、近似或新增容量门槛。

恢复以精确 Artifact 引用、冻结 snapshot 和主表/parts receipt 为唯一依据；不读取当前
Semantic 文件补齐定义，不按“最新产物”替代，不重算缺失状态。K 来自已验证方法状态与
部件，恢复前后既比较披露也实际执行。缺失、损坏或版本不符均拒绝。

### 3.4 Store、原子性和不明提交

在现有 Store owner 上切换一套新格式，方法状态单独版本化。旧 Store/Artifact 读前拒绝，
不迁移、不双读、不自动重建或删除用户 `.marivo`。错误引导使用明确的新状态环境，不能
建议覆盖历史成功结果。内部 SQLite 事务仅用于 Store，不取得业务查询资格。

复用 writer guard、原子发布与 reconcile。Run 状态、主表、全部 parts、receipt 和索引必须
有可定位的提交边界。失败只清理本 Run 拥有的临时资源；不覆盖成功产物或删除其他 Run 文件。
提交结果不明时先按原 Run/事务证据协调，不能重新读取源或重新执行算法。R4.1 明确列出
各故障点可观察状态与协调结果；无法确认成功时保留不明状态和修复依据，不伪报成功。

## 4. 分包实施顺序

每包先接受契约，随后迁入消费者、删除被替换实现并运行对应验证；中间分包不等于 R4 验收。

| 工作包 | 主要改动及责任 | 出口 |
| --- | --- | --- |
| R4.1 协议与交接冻结 | 核对 R3 core/methods/compiler 输出；盘点 `materialization`、`session`、公共 DSL、旧 Dataset 分派；冻结 identity/key、版本、检查证据、故障状态和删除矩阵 | 每个入口/codec/旧 key 路线有去向；所有协议字段有 owner；资格缺口有明确拒绝及恢复条件 |
| R4.2 通用节点与唯一执行协调 | 将 `observation/dsl_j1.py`、`public_dsl.py` 等消费者接入统一图；替换 `dsl_j1_runtime.py`/`execute_j1` 和 definition-only source cache；复用既有 Session/Runtime owner | source/fixed/mixed、跨 Session、节点共享与检查执行计数通过；无场景分派和执行后回退 |
| R4.3 统一交换与方法执行 | 接入 `source_stage.py`、`local_stage.py` 与 adapter；拆解 `dsl_j4_source.py`，已注册方法消费同一语义；统一 source/Parquet/pandas 生产者 | schema/Cell/键/状态向量一致，资源关闭及未耗尽拒绝通过；fixed-only 禁用 DuckDB 通过 |
| R4.4 统一产物与原子发布 | 将 `dsl_j1_artifact.py`、`dsl_j1_receipt.py`、`dsl_public_snapshot.py` 职责迁入通用 descriptor/receipt/snapshot 与方法 codec；更新现有 Store、publication、writer guard、reconciliation | 单一新格式，旧格式读前拒绝；各发布故障点不出现成功半成品，提交不明不重放 |
| R4.5 精确恢复和披露 | 接入恢复、`session.artifact`、retained reads 和当前结果 `repr/show/contract`；更新静态类型、Help/CLI、英中 latest 示例 | 断源新进程恢复同一 K，当前定义变更不修补固定结果；缺部件/receipt 的拒绝与建议一致 |
| R4.6 J1–J4 与安装包收口 | 同一候选 wheel 重建四条旅程，执行独立 oracle、身份、共享、故障与冷恢复；扫描产品、包与协议残留 | 验收矩阵逐格记录，旧链/alias/双读删除，后续阶段交接明确 |

以上路径是现有定位入口，不要求按名称机械创建对应新模块。实施时反查 import、调用、
注册、存储字段和 Help 消费者，补齐漏项；迁出文件删除后不得保留转发 shim。
跨文件 key/Run/receipt/Store 切换必须形成可执行的完整协议闭环，不能让中间版本写出混合格式。

## 5. 验收矩阵与独立预期

以下是待执行门禁，起草时均为未验证；历史绿色测试不预填本表。

| ID | 场景 | 必须观察到的结果 |
| --- | --- | --- |
| V01 | 构造/计划、mixed、跨 Session、不合格输入 | 无业务 I/O，无 Run；具体错误与修复，不隐藏执行 fallback |
| V02 | source 重复执行、改源、显式共享及独立节点 | 新求值身份；真实读取/实现次数符合节点语义，跨 execute 不复用来源 |
| V03 | fixed 命中/未命中及 key 单字段扰动 | 精确命中无 Run；顺序/绑定/方法/receipt/parts 变化不误命中；损坏拒绝 |
| V04 | 空流、Cell、完整键、类型边界、分批/换序 | 三生产者使用共同向量，保持精度与状态；重复键/损坏拒绝 |
| V05 | 检查作用域、未耗尽、取消、实现失败 | 义务不丢失不串组；无 completed 伪证据，无发布、换路或资源泄漏 |
| V06 | 主表/parts 写入、receipt/索引/提交故障和进程退出 | 原子性与 Run 状态一致；协调不重放、不覆盖、不越权清理 |
| V07 | 两 writer 竞争、锁释放、同 key 并发 | 唯一成功发布和确定冲突处理，无重复成功或他人文件损坏 |
| V08 | 新 writer→新进程→断源恢复→所有承诺 K | 精确引用、域/单位/Cell/状态/部件一致；禁用源连接和 DuckDB 仍通过 |
| V09 | 旧 generation、缺文件、坏 hash、错误 snapshot/状态版本 | 读前/使用前结构化拒绝；不迁移、不删旧状态、不加载当前定义补齐 |
| V10 | J1–J4 新统一链 | 数值、完整域、方法语义、状态、部件及动态 K 全部匹配独立 oracle |
| V11 | 公共类型/Help/CLI/英中示例与移除符号 | 正负 typing、导出快照、独立可达性/预算/漂移检查通过；旧入口不可用 |
| V12 | 隔离 wheel 与产品残留扫描 | 四条旅程在同一新安装包重跑，来源指向 site-packages；无场景执行链/旧协议兼容 |

J1 保留分层收入、合法空贡献与 Null；J2 保留比较后筛选成员再观察及端点绑定；J3 保留
不同粒度多根、完整元组并集、ratio 原组件与当前行统计的区别；J4 保留配对域、平均秩、
Null/不足对/常量及 coefficient 固定续算。复用原始事实和 Fraction/平均秩等独立 oracle，
不得以两个调用同一实现的结果互相证明。加入非电商字段/单位映射，排除场景硬编码。

## 6. 检查命令、证据与交付

测试实施遵守 `marivo-test-fixtures` skill，复用共享 fixtures。先按变更范围运行：

```sh
make test TESTS='tests/test_analysis_dsl_execution_identity.py tests/test_analysis_dsl_exchange.py tests/test_analysis_dsl_contracts.py'
make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py tests/test_lazy_runtime_concurrency.py tests/test_lazy_reconciliation_snapshot.py'
make typecheck TYPECHECK_TARGETS='marivo/analysis'
make lint-agent LINT_TARGETS='marivo/analysis tests'
make check-agent
```

这些是当前可定位的起始测试；文件随场景链删除而重组时同步更新命令和矩阵映射，不为了
保留命令保留旧实现。默认测试不执行的 Runtime 项必须单独记账。按实际新增故障/冷恢复
测试补齐 V01–V12，不能把以上文件列表视为覆盖证明。公共文档变化后在 `site/` 执行
`npm run build`；API 文档由 `make check-agent` 等价门禁验证。

R4.6 在隔离环境安装同一候选 wheel，记录包 hash、依赖、`site-packages`/`direct_url.json`、
脚本、数据 digest、精确 Artifact/Run 引用、日志和退出状态。禁止源连接的恢复必须在新的
进程中执行；同进程 mock 不是替代。真实 Agent 证据另列，脚本成功不算 Agent 通过；完整
能力簇真实 Agent 与六后端资格仍由 R10/R9 收口。本阶段不运行完整 release-check、不为
普通验证启动 MinIO，不提交、推送或发布。

产品扫描覆盖类型、方法 ID、注册、协议字符串、分派、codec、包内容和调用图；J1–J4 只可
保留为历史文档/测试旅程标识。静态扫描与实际提交/读源计数互补，扫描零命中不单独证明
唯一执行链。未迁方法的旧 SQL 按 R7–R9 台账交接，不能作为 R4 已迁入路线的隐藏回退。

每格记录代码 SHA 与 diff digest、协议/方法版本、输入与后端资格、命令、独立预期、实际
结果、日志 hash 和可取得位置。通过、失败、阻塞、未验证、跳过分开；64 项历史 skip 逐项
保留 owner/恢复条件，R4 消费者已迁入的断言应恢复执行，不能批量取消或计为通过。

交付包含：owning specs 更新、唯一执行/新协议实现、旧链删除清单、测试与 wheel 证据、
主验收记录和 R5–R9 交接。Help、动态 guidance、CLI、latest 英中示例随公共契约同步。
packaged semantic/analysis skills 如需修改，按仓库规则在实际编辑前取得明确授权；本次
文档不编辑 skills，也不预先把未授权编辑列为已完成。

R4 仅在 V01–V12 的必需单元通过、四旅程在同一新包重新成立、所有承诺 K 断源恢复可执行、
场景执行链及旧协议兼容删除后收口。环境不足保留具体阻塞及重跑条件，不以降级实现、历史
附件或改写验收状态代替证据。
