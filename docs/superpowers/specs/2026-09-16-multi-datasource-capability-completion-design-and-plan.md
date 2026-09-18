# 非 DuckDB 数据源功能补足：设计与实施计划

日期：2026-09-16

状态：C0 与 C1 已完成；C1 的六后端列依赖、schema 诊断及列 comment 证据见 [C1 验收](2026-09-16-multisource-capability-c1-acceptance.md)。C2 已实现并通过六后端标量验收，见 [C2 验收](2026-09-16-multisource-capability-c2-acceptance.md)；普通 timestamp 时间谓词按用户确认归 C3a。C3–C10 未开始，后续能力仍须独立实施及验收。

C0 的当前能力、目标/排除、历史证据与本次只读环境探测见 [C0 验收](2026-09-16-multisource-capability-c0-acceptance.md)；后续各阶段的 owner、fixture、验证入口及进入条件见 [C0 实施计划清单](2026-09-16-multisource-capability-c0-implementation-plan.md)。

## 1. 目标与基线

上一轮多数据源交付已经完成五个非 DuckDB 后端的有边界执行及安装包验收。本轮目标是让常见业务表和指标能够直接进入既有 lazy Analysis 流程，并逐步补齐与 DuckDB 的方法差距。完成标准是具体后端、类型、方法和输入形态的真实执行证据，不是连接成功、编译成功或扩大白名单。

本计划的当前基线来自：

- [上一轮设计实施计划](2026-09-15-lazy-analysis-multi-datasource-design-and-plan.md)。C0 已修正其顶部过时摘要；精确能力仍以当前代码与各阶段验收范围为准。
- [Slice 7 方法验收](2026-09-16-multisource-slice-7-acceptance.md)。
- [Slice 8 安装包验收](2026-09-16-multisource-slice-8-acceptance.md)。
- [Analysis 当前契约](../../specs/analysis/python-analysis-design.md)、[Semantic 对象模型](../../specs/semantic/semantic-object-model.md)及[时间语义](../../specs/temporal-semantics.md)。
- 当前 `analysis/operators/scalar_support.py`、各后端 support 模块、implementation registry 及 execution adapter。

本文基于代码和已有验收记录，不包含一次新的实时 Dataset 验收。C0 仅补充当前只读账户/权限/元数据探测；各阶段实施前仍须记录实际提交、工作区变化和服务可用性，本轮完成记录与旧验收分开。

### 1.1 已具备的能力

五个后端均已支持合格输入上的基础聚合、mean、weighted mean、ratio、同源关系路径、原生 date 的单单位日/周/月/季/年分桶，以及部分 snapshot/validity 选择。已物化的充分状态支持正确的冷读和 rollup；完整聚合结果可进入既有本地 Forecast、Kendall、时间发现和非 Entity 对比归因。

这些能力不重复建设，也不因新增后端差异而引入第二套算子语义。

### 1.2 当前缺口

| 类别 | 当前边界 | 业务影响 |
| --- | --- | --- |
| 类型与依赖列 | 必要列仍以有限标量类型为主；C1 已统一必要列依赖 | 未使用复杂列的阻断已解除；常见必要 Boolean、UInt、时间戳或字符串包装类型仍受限 |
| 时间语义 | 主要支持原生 date；未覆盖 timestamp/timezone/DST 时间轴、多单位桶和语义日历 | 订单时间、小时趋势、跨时区报表等受阻 |
| 指标表达 | 直接列 measure 为主；Linear graph 和未解析精度的复合 Decimal 不支持 | 行级计算、组合指标和精确金额运算表达受限 |
| 物理表范围 | MySQL InnoDB、Trino Iceberg、ClickHouse 普通本地 MergeTree、SQLite main 普通表 | 已有数据库中不少表不能直接使用 |
| 高级方法 | exact distinct、分位数/分布、抽样、Entity 方法、Event/Lifecycle 等未实现远程路径 | 用户分析链在高级步骤中断 |
| 执行域组合 | 不支持远程 retained import 或任意跨 datasource join | 不能将本地产物上传远程接续计算；不等于所有本地续算均不可用 |

## 2. 范围与不可改变的约束

本轮按独立阶段实施；优先交付类型、时间、指标和常见物理表支持，再逐项推进高级方法。高级方法纳入路线图，但不得用低阶段验收替代其独立设计和验收。

保持以下契约：

1. 逻辑构造不连接数据源、不解析凭据、不查询元数据。静态可判定的不支持形态在 Run 准入前拒绝；依赖实际物理事实的限制在执行时验证、读取结果行前尽早拒绝。
2. 一个源阶段使用一个精确 datasource binding；物理后端不改变 Dataset 的业务定义身份或 Session 所有权。
3. 统一算子、注册和确定性选择机制；物理差异由具体 adapter 承担。不得增加独立的能力注册中心或渲染层支持清单。
4. 远程账户只读，不依赖临时表、上传、UDF、宏或清理 DDL。DuckDB 已有本地临时资源能力保留。
5. 失败不触发替代后端、隐式本地回退、近似、截断或重复提交。已有本地方法可以按原契约接收完整、允许转移的输入。
6. 保留身份、关系 fanout、版本、数值、单次求值及私有状态约束；即使主结果为空，必要断言也不得省略。
7. primary、parts、Evidence、Findings 原子发布；冷读、精确命中、失败恢复和私有状态边界保持不变。
8. 不增加跨查询共同快照、引擎版本认证、远程终止证明或 Marivo 执行预算；外部系统限制及原始错误正常传播。

以下不在本轮实施范围内：任意跨源联邦、远程 retained 上传、业务数据写入、资源调度器、成本优化器、新统计方法、任意 SQL 执行入口及与具体适配无关的依赖升级。JSON/文件作为新的远程 source 形态也不随“JSON 列支持”自动开放。

## 3. 设计

### 3.1 以完整计算依赖收敛类型检查范围

先解决“没有使用的列阻断执行”，再扩大具体类型覆盖。复用现有语义依赖图和 compiler normalization，得到按 Entity、精确物理关系区分的所需列集合。不得仅按列名全局合并，也不得只从最终输出投影反推。

依赖集合至少包含：

- measure、dimension、time dimension 表达式的全部输入列；
- Population 条件、Metric slice 和上游仍参与计算的隐藏指标；
- 主键、关系连接键、贡献粒度及身份/fanout 断言所需列；
- snapshot/validity 坐标、状态时间、时间解析及合法性断言所需列；
- retained components 和方法私有准备路径的真实源依赖。

列依赖由 compiler/normalization 统一产生，静态准入、Runtime 物理 schema 校验和源表投影消费同一份按 Entity、精确 datasource binding 与物理关系归属的依赖事实。该契约同时覆盖 PostgreSQL、MySQL、SQLite、Trino、ClickHouse 和 DuckDB 的声明表源路径；不得仅修补共享 scalar adapter。adapter 保留各自的元数据 SQL、关系解析、类型映射与流式传输方式，但只对本次所需列执行类型解析和执行相关检查。允许元数据接口返回全列描述，不得因此解析无关类型或读取无关数据列。不能通过忽略未知依赖放行；无法静态解析的表达式继续结构化拒绝。未引用列的变化不应新增执行限制，但不得借此跳过 Entity 身份或关系断言。

C1 同时统一 schema 失败诊断：必要列缺失、物理类型不支持和声明/实际类型不匹配必须可区分，携带 Entity/物理关系、逻辑列/源列、预期/实际类型及具体修复；缺失列的实际类型明确为缺失，不复用笼统的类型不匹配文本。诊断从已有依赖与物理元数据生成，不包含凭据或业务行值。

这仅收窄 Analysis 的执行相关检查，不直接削弱 datasource 声明、semantic load 或 readiness 自身的契约。若上游加载也拒绝某类声明，必须由其所属层单独解决；不得承诺只改 Analysis 即可接入任意复杂表。

### 3.2 分层扩展类型

区分三个事实：能够连接并发现类型、能够传输该类型、能够对该类型执行某种方法。任意一项成功都不等于其余两项成功。

| 优先级 | 类型范围 | 设计要求 |
| --- | --- | --- |
| T1 基础标量 | Boolean、有符号/无符号整数、常见字符类型与包装、普通 timestamp | 明确物理到逻辑映射、比较及排序语义、精度和 NULL；UInt64 不经 float 或有损 int64 转换 |
| T2 时间与精确数值 | 带时区或高精度 timestamp、明确 precision/scale 的 Decimal 组合 | 保留 instant/civil 区分；确定 Arrow/Parquet 与运算结果类型；溢出不能截断、饱和或静默舍入 |
| T3 复杂值 | JSON、Array、Map、Struct 等复杂值 | 先识别现有语义层可表达的闭合形态；分别验收投影、字段提取和聚合用途，不把可传输等同于可作为身份/分组键 |

T1/T2/T3 表示类型能力层级，不表示交付批次；第一批交付可以包含 T2 中满足目标方法所需的子集。

| 类型层级 | 实施阶段与范围 |
| --- | --- |
| T1 | C2 实现映射、传输和基础方法；C3 单独激活 timestamp 时间轴 |
| T2 时间类型 | C3 先补齐目标时区/精度的映射与传输，再激活时间方法；不假定 C2 已包含这些能力 |
| T2 Decimal | C4 实现结果精度推导、精确计算和 retained 续算 |
| T3 | 本轮保留为后续方向，不纳入 C0–C10 的必交矩阵；C1 只保证无关复杂列不阻断已有合法计算，使用复杂值的正向能力另立实施阶段 |

各类型层级按后端分别实现，不承诺所有引擎具备相同原生类型。SQLite 的 Boolean、timestamp 或 Decimal 若只能依赖约定存储，必须验证实际存储类别和值域，不能通过类型名称模拟原生语义。

ClickHouse 优先覆盖 UInt、Bool、LowCardinality(String)、DateTime/DateTime64 及其合法 Nullable 包装；字符串包装只在不改变值、NULL、比较和排序契约时归一化。MySQL Boolean 别名必须与普通整数区分，不能将任意非零值自动解释为合法 Boolean。

Decimal 沿用语义层结果类型规则，明确每步运算及聚合的 precision/scale、除零和溢出行为。后端精度不足时保留拒绝，不引入全局 float 降级。更高精度仅在逻辑类型、运算、传输、持久化和冷读全链均支持后开放。

### 3.3 时间轴与版本语义

复用现有时间语义所有者，不新增 adapter 自定义的业务时区默认值。源时间、业务时区、输出桶坐标和边界解释必须一致。

C3a 将六后端的时区解析策略收敛到现有时间 owner：adapter 负责获取实际引擎事实，共同策略决定显式 parser、引擎时区与允许的显式系统 fallback 的优先级、失败和来源记录。独立实施计划必须先区分无引擎时区能力、查询失败、无效时区、固定偏移及合法 IANA 时区，确定每种情形的处理和诊断；不得由 adapter 自行 catch 后改用主机时区，也不得机械改成全部 fallback 或全部拒绝。已有 civil-date、显式 parser 和冷读时间事实不能退化；具体策略在激活 timestamp 前与 owner 契约对齐。

按以下顺序推进：C3a 负责普通 timestamp 和小时/日桶、时区与 DST，并包含所需的 T2 时间类型支持；C3b 负责既有解析声明支持的字符串时间和多单位桶。语义日历、累计、status-time fold 和更完整 validity 形态归 C6，不计入 C3。C3a 与 C3b 分别验收，不能用 C3a 完成记录标记整个 C3 完成。

时间戳类型准入与时间方法准入分别记录。覆盖精度边界、跨日/周/年、DST 重复和缺失时间，以及 validity 的端点与 open-end 语义。解析失败、模糊时间或精度损失必须按现有契约失败；不得隐式 cast 为 date、依赖服务器默认时区或取近似桶。

### 3.4 计算型 measure 与 Linear Metric graph

继续使用受限 Ibis 表达式和现有 semantic validator，不开放 SQL 文本执行。先覆盖现有语义层已经允许的行级算术、显式 cast、条件和 NULL 处理，再接通 Linear graph。

准入检查实际表达式依赖及结果类型；不能只删除 `source_column` 限制。跨行聚合、窗口、易变表达式和额外关系依赖不作为普通行表达式放行。后端编译成功仍需验证数值语义。

Linear graph 的组件、单位、NULL/空集及系数规则由现有 Metric 契约决定。必须保留足够的 retained components，使后续 rollup 仍按原方程重算，不使用显示值重新推导。

### 3.5 物理表与 connector 扩展

表支持仍由 adapter 验证真实元数据，不能仅根据表名、catalog 名或引擎名称后缀猜测。

| 后端 | 后续目标 | 开放条件 |
| --- | --- | --- |
| MySQL | 更多实际使用的文本 collation、普通视图 | 证明等值、分组、排序及尾随空格语义；不能用 binary cast 偷换原契约 |
| SQLite | 普通视图；有明确需求时再考虑 attached/virtual table | 查询只读、依赖关系和实际存储语义可验证；扩展加载不自动授权 |
| Trino | 不限制 catalog 名称或 connector 类型，不以 Iceberg 或其他 connector 白名单决定准入 | 按实际列类型、表达式、关系形态、只读权限和执行语义校验；用 Iceberg 与非 Iceberg catalog 的真实旅程验证通用路径，实际覆盖范围单独记录 |
| ClickHouse | Distributed 分布式表纳入第一批必交目标；ReplicatedMergeTree、ReplacingMergeTree 仍另行选择 | 真实多分片验证全局聚合、跨分片重复身份、关系 fanout、分片与副本语义、传输及故障；不隐式加 FINAL 或去重 |
| PostgreSQL | C0 核实目标视图、分区表已有支持及证据；类型扩展归 C2–C4 | 本行是基线核验项，不预设功能缺失；仅当核验发现具体缺口且纳入目标矩阵后，才进入 C5 实施范围 |

ReplacingMergeTree 等表若需要业务级去重或版本选择，由既有语义模型显式表达；普通读取不能承诺后台合并已经完成。Distributed 的分布式执行也不能改变精确聚合和身份规则。

C5 已锁定 ClickHouse Distributed 和 Trino 不按 catalog/connector 类型设白名单两个目标。Trino 的具体非 Iceberg 测试 catalog 在 C5 独立计划中选定，它是验收样本而非支持白名单；元数据检查不得继续依赖 Iceberg 专属接口。ClickHouse 须准备真实多分片环境，单节点 MergeTree 不能代替分布式验收。没有环境时记录阻塞并保持目标未完成，不将已锁定目标移出第一批，也不以模拟结果替代。

### 3.6 高级方法的独立实施边界

| 方法组 | 必须解决的核心问题 | 不能采用的捷径 |
| --- | --- | --- |
| exact distinct | 源端精确 membership、重叠组 rollup、私有状态生命周期 | 仅返回 COUNT DISTINCT 并丢弃续算状态；改用近似计数 |
| quantile/distribution | 既有精确定义、插值、分布状态和续算 | 用引擎默认近似分位数替换 |
| sampling | 同一选择被主结果、断言及 parts 复用，满足原有抽样语义 | 每次查询重新随机；仅凭 CTE 认为单次求值成立 |
| Entity correlation/candidate、driver screening | 私有身份、配对/筛选准备、精确数值及允许的状态流向 | 将原始身份和私有中间行转入本地绕过源端限制 |
| 扩展归因 | 隐藏轴准备、完整对齐与贡献状态 | 根据可见 Top-N 或截断结果归因 |
| Event/Lifecycle | 事件顺序、并列规则、匹配、状态重放与单次求值 | 用普通聚合近似事件过程 |

每组先建立最小后端实现和独立期望值，再逐后端复制验收。若只读能力不能满足必要的准备或求值约束，该后端保持拒绝并记录阻塞原因。不得因此开放远程写入、改变方法定义或宣称全功能对等。

### 3.7 统一实际执行记录

C3a 一并修正时区查询的双重事实来源：Runtime 不再根据 profile 预先制造“已执行 SQL”，记录由 adapter 的实际提交边界产生。将相同记录约定覆盖六后端的元数据、validation、primary、parts 和本地续算路径，避免某些查询预记、某些查询漏记或重复记账；C10 负责最终安装包的独立核对，而不是延后实现。

记录保留实际提交的 SQL 文本与语句角色，不重新编译或通过字符串归一化掩盖差异。参数的处理沿用安全证据边界，凭据和私有行值不得进入回执。提交尝试、执行成功和失败必须区分；编译或提交前失败不能当成成功执行，未捕获的驱动内部操作明确标记为覆盖限制。计数从同一提交事实按既有口径归类，保留 metadata 与业务断言的区别，不以操作调用数冒充物理 SQL 数量。

这项统一不要求统一游标、binary RECORD、标量身份展开、引擎数值转换、取消或清理的物理机制，也不增加远程终止证明、共同快照、重试或新的监控系统。

## 4. 代码归属与披露

- `marivo/semantic/`：逻辑类型、表达式、指标和时间业务契约；不携带特定数据库执行逻辑。
- `marivo/datasource/engines/`：连接、物理元数据和类型事实；不学习 Analysis 方法列表。
- `marivo/analysis/compiler/`：完整依赖、语义 lowering 和 placement；不引入另一套查询优化器。
- `marivo/analysis/operators/`：现有注册机制中的精确方法/后端/形态准入及拒绝原因。
- `marivo/analysis/materialization/`：后端 SQL、准备、类型转换、流式传输、取消和资源生命周期。
- 现有 Help、动态 contract、结构化错误及文档：披露已经激活的能力，提案不得提前进入成功提示。

共享逻辑仅在多后端具有同一语义时提取；避免布尔开关不断增长或新增万能能力对象。错误应指出具体依赖、类型、方法或物理关系，以及真实可行的修复；不能建议静默降精度或更换执行器绕过边界。

每次激活同步更新当前 owner spec、Help、动态指导、独立漂移/可达性/预算测试、相关示例、CLI/skill 及中英文 latest 文档。涉及 marivo-semantic 或 marivo-analysis packaged skill 编辑时，遵守当前 AGENTS.md 的显式用户批准要求；本文不自动授权该编辑。保留一个事实所有者，不把本文变成第二份运行时能力表。本文为中文设计文档；后续代码、注释、测试、fixture 和代码中的用户字符串继续遵守仓库英文规则。

## 5. 分阶段实施

C0 为后续阶段建立实施计划清单；每个阶段执行前须补齐该阶段的独立实施计划，包含精确后端/方法/类型范围、拥有者文件与符号、复用的 fixture、测试文件、具体验证命令和排除项。只写“运行相关测试”不能替代可执行命令；本总计划不要求提前猜测后续阶段尚未确定的函数名。

以下为 C0 核查时定位的 C1 改动入口；这些全列检查已由 [C1 实施及验收](2026-09-16-multisource-capability-c1-acceptance.md) 改为统一必要列依赖。原入口是 `analysis/operators/scalar_support.py::unsupported_reason()` 中遍历 `entity.columns` 的全声明列检查，以及 `analysis/materialization/scalar_sql_execution.py::ScalarExecutionAdapter.prepare_dataset()` 中生成 `_declared_columns` 的事实供应。后者目前按列名合并，实施时须改为关系归属明确的依赖事实，并追踪各 adapter 的 schema 消费路径。PostgreSQL 与 DuckDB 独立 adapter 也须覆盖，不能只修改共享 scalar adapter。Runtime 的 `materialization/admission.py::DatasetRuntime._validate_source_schema()` 和 `_declared_table()` 当前仍遍历全部声明列，必须同步接入统一依赖事实。现有 `compiler/normalize.py::required_entities()` 可用于定位 Entity，但不等于已经具备完整列依赖分析。不要求将所有 adapter 合并为同一继承体系，也不把只读限制施加到 DuckDB 已有本地临时资源、文件和 retained 路径。

以下编号使用 C 前缀，与上一轮 Slice 0–8 区分。每个阶段可以拆成逐后端子任务，实施记录必须标明具体启用单元及剩余拒绝项。

| 阶段 | 范围与交付 | 前置条件 | 完成标准 |
| --- | --- | --- | --- |
| C0 基线与目标矩阵（已完成） | 核对当前注册、类型、物理表和实测环境；修正原计划过时的进度摘要；确定第一批目标及后续实施计划清单 | 无 | 已交付可追溯矩阵与计划清单；未验证项明确标记，后续阶段细化后方可执行 |
| C1 统一列依赖契约（已完成） | 六后端声明表源按关系归属计算完整所需列，统一静态准入、Runtime schema 校验、adapter 类型解析和源投影 | C0 | 已声明未使用列及未声明物理列均不引入无关执行限制；隐藏计算、身份、关系和版本依赖仍被正确检查，schema 错误提供具体依赖/类型事实，DuckDB 既有能力不退化 |
| C2 常用标量类型 | Boolean、UInt、字符串包装、普通 timestamp 的精确传输和基础方法 | C1 | 每个目标后端的极值、NULL、排序、聚合及冷读通过；不自动激活时间轴 |
| C3 时间分析 | C3a：T2 时间类型、timestamp 时间轴、小时桶、统一时区策略与实际执行记录；C3b：解析和多单位桶 | C2、时间 owner 契约核对；先验证所需类型再启用方法 | 每个子范围具有边界矩阵、真实执行和独立期望值 |
| C4 计算与精确数值 | 计算型 measure、Linear graph、可解析精度的 Decimal 组合 | C1、相关 C2 类型 | 源计算、断言、components、rollup 和冷读数值一致 |
| C5 常见表形态 | 按 3.5 节逐表引擎/connector 实施 | C0、所需类型已支持 | 真实只读账户、物理语义、失败路径和普通业务旅程验收 |
| C6 完整时间状态 | 日历、累计、status-time fold、剩余 validity 形态 | C3、C4 所需状态 | 空桶、端点、重叠、跨期和 retained 续算满足原契约 |
| C7 精确集合与分布 | exact distinct，随后 quantile/distribution | 所需类型及私有状态设计 | 源端实现、重叠组续算、隐私边界和独立数值全部通过 |
| C8 抽样与 Entity 方法 | sampling、Entity correlation/candidate、driver、扩展归因逐方法实现 | 各自准备路径可行性已证明 | 单次求值、身份/对齐、私有状态和失败清理通过 |
| C9 Event/Lifecycle | 先事件匹配，再状态重放及后续分析 | 排序/时间/准备能力已实现 | 并列、乱序、重复、边界和完整重放验收 |
| C10 安装包整体验收 | 最终 wheel 上验证已启用单元、冷进程和负向矩阵，汇总剩余缺口 | 本次约定交付阶段完成 | 安装来源/hash 可验证，真实旅程通过，无越界支持声明 |

已确认第一批交付为 C0、C1、C2、C3a、C4，以及已明确锁定的 C5 单元，并由 C10 做有界安装包验收。C5 已锁定 ClickHouse Distributed 分布式表和 Trino 不限制 catalog/connector 类型，纳入首批承诺；其测试拓扑与代表性 catalog 在 C5 独立计划中明确。其他表引擎或视图仍待选，C5 本身尚未完成。逐后端类型/方法目标见 C0 验收矩阵。C3b 后续单独交付，不算第一批完成条件，也不能提前标记完成。C6–C9 不混入第一批提交。C5 可在相关类型准备好后独立推进，但不以并行执行重型数据库测试节省时间。

C10 可用于第一批的有界验收，不能因此标记 C6–C9 完成。完整补足目标只有在约定矩阵全部实现时才完成；仍然拒绝的单元必须保留在最终差距表中。

## 6. 验收设计

### 6.1 通用必测项

1. 构造无 I/O；已知不支持项在 Run 前拒绝；实际 schema 问题产生准确诊断且不发布结果。
2. 独立手算或独立参考实现为主判据，DuckDB 作为额外对照。不能只比较两个后端的共同错误。
3. 在实际提交边界捕获 SQL、按安全边界处理的参数及语句角色，分别统计 metadata、validation、primary、parts 和本地续算；独立观察器核对文本、角色、次数与成功/失败，不依赖同一记录函数自证。SQL 含谓词不能充当物理分区裁剪证据。
4. 验证最大合法源端连续计算链。大输入/小输出用有界 fixture 证明聚合下推，不将原始 Entity 全量搬到本地。
5. 空输出、NULL、重复身份、关系 fanout、非法值、零分母、溢出和类型漂移均不能绕过必要断言。
6. 原子发布、冷进程续算、移除源后的命中、失败不发布、取消和断连后资源清理保持成立。
7. 捕获只读账户下的连接、元数据、查询与清理操作；不以单次 INSERT 拒绝代替全部权限路径验证。
8. 新增成功单元对应明确的负向邻接项，例如支持 DateTime64 某精度不等于所有精度或全部时间方法可用。

### 6.2 重点回归矩阵

| 领域 | 必测样例 |
| --- | --- |
| 依赖裁剪 | 未使用 JSON 列；同名列出现在不同关系；隐藏 measure；只用于主键/关系/validity 的列；未知表达式依赖 |
| 整数与 Boolean | UInt64 最大值、超过 int64 的身份值、SUM 溢出、0/1/NULL 及非法 Boolean 存储 |
| 字符串 | 大小写、重音、尾随空格、空串、NULL、确定性并列排序、LowCardinality/Nullable 组合 |
| Decimal | 大整数部分、不同 scale、乘除组合、零分母、结果精度增长、源/Arrow/Parquet/冷读一致性 |
| 时间 | 毫秒/微秒及目标高精度、非整点时区、DST gap/fold、跨年周、月末、闰日和边界包含性；六后端无时区能力、探测失败、非法名称、固定偏移、显式 parser 优先级与冷读来源 |
| schema 诊断与回执 | 缺列/错类型/不支持类型可区分且指明关系和列；真实 SQL 与记录逐条匹配；无查询不记成功、失败保留原始原因、不漏记或重复记账 |
| 指标 | 条件和 NULL 行表达式、多组件 Linear、关系贡献粒度、显示结果投影后 components 仍可 rollup |
| 物理表 | 实际 connector 身份、视图依赖、Replacing 重复行、Distributed 跨分片身份及聚合 |
| 私有状态 | 重叠集合、相同分位数但不同分布、抽样复用、事件并列和重复、禁止私有身份导出 |

### 6.3 命令与证据

实施时使用仓库入口；修改测试前遵循 `marivo-test-fixtures` skill。先跑最窄的 `make test TESTS='...'`，再跑必要的 `make runtime-test TESTS='...'`，随后运行触及模块的 `make typecheck TYPECHECK_TARGETS='...'` 和 `make lint-agent LINT_TARGETS='...'`。共享行为阶段运行 `make check-agent`，披露变更补充相关 site 检查。

实时数据库和重型门禁串行执行；普通开发不启动完整 release Runtime 或 MinIO。最终安装包验收与源码测试分别记录，未运行的检查必须明确写出。提交、推送和发布不由本文自动授权。

验收文件统一放在 `docs/superpowers/specs/`：`YYYY-MM-DD-multisource-capability-cN-acceptance.md`；C3 子阶段分别使用 `c3a`、`c3b`。日期使用实际验收日期，逐后端记录可增加后端名后缀。需要机器可读证据时使用同前缀的 `-execution-receipts.json`，由验收文档链接；不得覆盖上一轮 `multisource-slice-N` 记录。阶段实施计划使用同前缀的 `-implementation-plan.md`。C10 汇总并链接各阶段记录，区分已完成、未运行和仍拒绝的单元。

每阶段验收记录包括：代码提交/工作区身份、目标能力单元、fixture、真实账户边界、执行语句角色、传输行数/字节、独立预期值、失败及清理结果、运行命令与退出状态、剩余拒绝项。版本仅作诊断，不作为准入认证。

## 7. 风险和完成判定

主要风险是依赖裁剪漏掉必要断言、类型归一化改变数值或排序、时间解释依赖环境默认值，以及高级方法看似成功但丢失 retained 状态。分别以完整依赖图、端到端类型测试、明确时间契约和跨进程续算验证控制。

所有推进遵循“先证明现有契约能在该后端实现，再激活精确单元”。若需要新增公共类型或修改业务语义，先更新其 owning contract 和本计划对应范围，不在 adapter 中私自定义。

第一批完成意味着常见业务类型、时间轴、计算指标和选定表形态具有安装包级真实旅程；不意味着所有复杂类型和高级方法都已支持，也不意味着每个 Trino connector 均已逐一实测；Trino 准入不按 connector 类型设白名单。完整功能对等是矩阵中的全部目标单元通过，而不是所有后端名称都出现在 registry 中。
