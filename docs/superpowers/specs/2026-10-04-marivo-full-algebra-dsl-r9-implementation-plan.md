# Marivo 全量分析代数与 Analysis DSL：R9 实施文档

Date: 2026-10-04

Status: R9.1 static handoff implemented; validation is recorded in the
[R9 evidence index](2026-10-04-marivo-r9-evidence-index.md). R9.2 implementation
and bounded source evidence are recorded in the
[R9.2 record](2026-10-04-marivo-r92-evidence/README.md): 42 original success
scenarios passed. On 2026-10-06 the user authorized SQLite native Decimal to be
unsupported, with exact refusal under its unchanged requirement ID. Two actual
refusal paths passed; the amended owning exit is 42 successes plus one exact
refusal, with original evidence retained. Final current-candidate impact belongs
to the R9.7 audit;
R9.3's original 222 representative implementation requirements are closed by
the [full-grid Unknown closure](2026-10-04-marivo-r93-evidence/full-grid-unknown-01/README.md);
this is bounded implementation acceptance, not final all-profile qualification.
R9.4's scoped graph/recovery/resource evidence is recorded in its
[current record](2026-10-06-marivo-r94-evidence/README.md). R9.5 SQL ownership and
physical retirement are recorded in the
[current SQL ledger](2026-10-06-marivo-r95-sql-ledger.md). R9.6 implementation and
cost boundaries are recorded in the
[current cost record](2026-10-06-marivo-r96-cost-record.md); the
[R9.7 completion audit](2026-10-06-marivo-r97-completion-audit.md) remains separate.
Frozen targets grant no execution qualification.

## 1. 目标、前置交接与文档权威

执行[总实施计划 R9](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md)，
闭合六后端的真实方法资格、生产 SQL 来源与运行代价证据。复用现有 typed graph、唯一
method registry、datasource adapters、Runtime、受控 Arrow/Parquet 交换和 Store 7；
不建设第二套后端执行器或独立支持矩阵，不改变 R5–R8 已接受的方法含义。

用户确认 R0/R1/R2/R3/R4/R5/R6/R7/R8 任务完成，以此启动 R9 规划。起草分支 `panda`，
初次读取 HEAD 为 `7ecf92daea9f13ee04b9ca4c046a191d5b3e680f`，当时工作树已有 R8.6
产品、测试、文档和证据改动。定稿复核时，前序改动已另行提交为
`555b8ac5adda14b53cdc774ba1cf1fb5f760ffe5`，工作树仅余本文未跟踪。本次仅新增本文，
不整理或提交前序改动。实际实施 R9.1 时重新绑定代码 SHA、dirty diff、新增文件摘要、
依赖和契约版本，不能仅以本段 HEAD 指代候选实现。

[主验收记录](2026-09-26-marivo-full-refactor-acceptance.md)与
[R8.6 evidence index](2026-10-04-marivo-r86-evidence-index.md)仍保留有界通过、阻塞及
未验证记录；用户的阶段完成交接与这些历史证据分别保存。本文不改写历史分母，也不以
用户交接补授六后端资格。R9.1 将必需格绑定到实际消费者和证据；依赖缺口保留 owner、
精确复现与阻塞状态，其他独立格可以继续，受影响格不能先登记通过。

### 1.1 范围与阶段边界

| 范围 | R9 必需交付 | 边界 |
| --- | --- | --- |
| 六后端 | DuckDB、PostgreSQL、MySQL、SQLite、Trino、ClickHouse；当前支持的物理形态分别验证 | 连接成功、metadata 成功或旧 Dataset 测试不授新方法资格 |
| C01–C15、C18 的运行相关目标 | 基础读取、完整域、数值/领域方法、检查、交换、发布、固定续算与冷恢复 | C02 验证定义向执行的绑定；不重写 authoring 设计 |
| 物理实现补齐 | 为冻结必需格修复 adapter、注册、Ibis lowering、解码与资源 owner | 无合法实现的必需格保持阻塞；不能改成永久不支持以缩小分母 |
| SQL 治理 | DS/AN 台账闭合、实际提交审计、已批准例外与 Store 事务单列 | 清除未批准生产 SQL；保留下节限定的 R1.6 例外和公共终端 |
| 运行代价与扩展 | 1,000/100,000 事实、方法必要压力反例、三类路线成本及扩展改动位置 | 观测成本，不新增输入行数、字节或内存 Runtime 配额 |
| C16/C17 增量 | 修复影响到的类型、Help、repair、CLI、依赖隔离及当前中英示例 | 全库公共收口、最终 wheel、真实 Agent 和发布仍由 R10 负责 |

不新增联邦 join、跨源上传、显式 Artifact+现场来源 mixed、任意 callback、自动 planner、
bootstrap/因果/生存推断。已撤回的 statistical_weight authoring 不恢复；已有获准固定
权重消费按当前 owner 验证。无需为 R9 引入第七种生产后端或通用成本优化器。
本次编写文档不启动数据库、MinIO 或 release-check，不修改 AGENTS.md 或 packaged skills。

### 1.2 唯一契约 owner 与有效修订

| Owner | 本阶段消费的权威 |
| --- | --- |
| [总实施计划](2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md) §5、R9、§10–§13 | 六后端目标、方法验收、成本与完整交付边界 |
| [R0 capability ledger](2026-09-26-marivo-full-refactor-r0-capability-ledger.md) | C01–C18、方法/规则与迁移责任；研究扩展不混入必需目标 |
| [R0 SQL ledger](2026-09-26-marivo-full-refactor-r0-sql-ledger.md) | DS/AN 提交链、六后端目标与 R1.6/R7.9 当前覆盖；历史条目不是当前实现清单 |
| [Datasource layer](../../specs/semantic/datasource-layer.md) | typed source、物理事实、provider 例外、凭据、只读/超时及 raw SQL 终端 |
| [Python Analysis design](../../specs/analysis/python-analysis-design.md) 与 [Operators](../../specs/analysis/operators-and-frames.md) | 具体类型、方法含义、Cell、域、数值政策、Pre/RequiredParts/K 和结构化拒绝 |
| [Session/Runtime](../../specs/analysis/session-state-and-runtime.md) | 来源新求值、共享、固定命中、检查期限、资源、Store 7 与断源恢复 |
| [Timezone/calendar](../../specs/analysis/timezone-and-calendar-design.md) 与 [DSL architecture](2026-09-24-marivo-analysis-dsl-architecture-design.md) | 时间精度、日历邻接、输入边界、统一 deadline 与无容量准入政策 |
| [R5 ledger](2026-09-28-marivo-full-algebra-dsl-r5-migration-ledger.md)、[R6 ledger](2026-09-30-marivo-full-algebra-dsl-r6-migration-ledger.md)、[R7 ledger](2026-10-01-marivo-full-algebra-dsl-r7-migration-ledger.md)、[R8 ledger](2026-10-03-marivo-full-algebra-dsl-r8-migration-ledger.md) | 实际方法消费者、部件、退役与各阶段未授予的后端资格 |

两个后续接受的修订优先于主计划中的早期摘要：

1. R1.6 于 2026-09-28 已获用户批准的 provider 固定语句通道，限于六后端 metadata
   事实和 DuckDB scoped HTTP 凭据安装。保留封闭注册、模板快照、用途/参数审计和脱敏；
   不授权 Analysis 算法、业务完整性、类型/时间修正或新控制 SQL。SQL 清零的判定是
   **未批准生产 SQL 为零**，不能误删已批准能力，也不能将“provider 内部”当作豁免。
2. R1.3 接受的 `md.raw_sql` 原文提交不做 SQL 解析/诊断分类；只读依连接与后端权限
   尽力控制。必填 reason、正数返回行界、可执行 timeout、截断披露及不可重入照常验证。
   不重新引入早期摘要中的 SQL 文本分类器或把只读尽力控制写成绝对保证。

2026-10-05 用户另行批准了限定的 MySQL 控制例外：认证连接的会话级 SELECT
截止时间安装/读回，以及当前 SourceSession 自有查询的 `KILL QUERY`。精确边界分别由
[认证超时方案](2026-10-04-marivo-r93-mysql-authoring-timeout-proposal.md)和
[自有查询取消方案](2026-10-04-marivo-r93-mysql-cancellation-proposal.md)拥有。
批准不替代实现、实际提交审计或资格证据，也不扩展到全局设置或跨 Session 查询。

## 2. 冻结资格单元与证据规则

### 2.1 单元键、必需集合和拒绝集合

以当前 `methods.physical.QualificationKey` 为产品选择键：

```text
method@version
  × ordered input types and domains
  × SourceShape(backend, form, table_kind, exact time shape) or FixedShape
  × route(ibis | ibis_python | artifact_python)
```

验收记录在该键之外还绑定 implementation/version、数值政策、完整主体键、定义版本、
参数/路径、来源与表拓扑、覆盖、RequiredParts、检查义务和 proof class。服务/驱动/Ibis
版本及非敏感配置属于证据环境，不能省略后将一个部署结果泛化到所有部署。
`view`、namespace、HTTP 认证和 Distributed 拓扑必须保留可区分的物理 profile，不能
在记录或 resolver 中统一改成 `native` 来借用普通表证据。

R9.1 从 R0 冻结目标与前序迁移账本生成不可变 requirement IDs，划分：

- **必需成功格**：接受的能力/形状必须获得合法路线和真实正例；当前拒绝不是完成证据。
- **必需拒绝格**：owning contract 明确不准入的输入或续算，必须验证结构化拒绝及期限。
- **不适用**：只能由既有契约说明，如无时间方法不展开 DST；不计成功数，也不掩盖缺实现。

不盲目做所有参数的笛卡尔积，也不只选 happy path。按 owner 的适用规则展开，保留
每个原目标到 requirement ID 的映射及排除理由；方法、类型、形态、路线和关键义务均
可追溯。预期通过格没有实现时仍在原集合；新增发现的提交入口、形态或反例追加 ID。
范围修订须有接受依据、旧新集合和差异，不能原地覆盖分母或历史状态。

R8.6 的 `2026-10-04-marivo-r86-evidence/r9-targets.json.gz` 是静态交接种子，起草时有
9,109 个 profile，状态为 unverified。它不能替代 C01–C13/C15/C18 目标，也不能把
同 profile 的本地成功复制到六后端。R9.1 校验种子 hash、版本和 owner，并按适用物理
形态展开；profile 数不等于最终 requirement 数或已执行数。

### 2.1.1 已接受的 R9.1 场景粒度修订（2026-10-04）

用户在实施过程中确认采用“方法族与关键风险场景”，不采用逐种子×后端×形态×路线
的机械展开。本节优先于本文早期关于种子逐格展开的表述；保留 R0 及前序任务的原目标
和历史分母，不把约 35 万条未接受的草稿记录作为新的验收分母。

- 9,109 个统计种子完整保留摘要与多对多追溯映射；种子不是独立测试任务，代表场景
  通过也不授予每个种子或任意 precision/scale/时间形状资格。
- 六后端分别验证基础来源与必需方法路线。共享方法核按算法类、数值边界、完整域、
  状态与关键反例验证；特殊物理形态分别验证 metadata、捕获、解码和资源差异。
- fixed 目标无执行后端，只登记一次。各真实 producer 的 schema/receipt/parts/断源
  绑定仍须取得证据，不能用一个 producer 代授其他 producer。
- 历史账本行仅作 owner/目标追溯，不增加验收任务。SQL ID、V01–V17、拒绝期限、
  成本规模和压力算法仍保留。方法或后端形态的实际差异若影响语义、解码或资源，
  在原场景集合上追加精确 ID；不预造所有组合，也不删除发现的缺口。

R9.1 冻结的是可执行场景目录及其覆盖责任；R9.2–R9.6 绑定实际消费者、oracle、命令、
环境及具体 QualificationKey。未绑定项保持 unverified 并列出后续 owner。最终完成判定
使用这些场景及实施中追加的实际反例，且只披露实测 key/profile 的资格。

### 2.2 三条路线的独立资格

| 路线 | 必需证据 | 不能替代的义务 |
| --- | --- | --- |
| `ibis` | 纯计划选择、Ibis 表达式、session 签发、未经改写的实际 driver 提交、完整输出/检查 | 仅 compile 不证明执行、数值或资源；source 失败不重选 |
| `ibis_python` | 执行前选择；完整身份/范围/Cell/parts 的 Ibis 准备，所有必要源依赖准备后本地核消费 | 本地核通过不证明该后端捕获；不能先选择本地再补源依赖或只取首批 |
| `artifact_python` | receipt/part 校验、受控 Arrow/Parquet→pandas/数值核、固定继续与 exact hit | 禁止当前 Semantic、来源连接、DuckDB 扫描、远端上传或重新拟合已有结果 |

R9.6 已批准弱一致性：source check 与后续原生计算可读取不同源版本，check 证据只证明
自身查询，不证明后来读取的事实。一个逻辑节点/Run/result 身份不承诺一次物理扫描；
纯原生图直接查询封闭 Ibis 表达式，不为复用预检事实而强制 Arrow 中间结果回源。
最终主表、RequiredParts、完整键、Cell 和内部算术仍须校验；失败不换路线。

R0 指定的必需 P/F 格分别取得证据；新增 native Ibis 成功不能删除原 P 格。
基础成员/属性、观察、完整域、多根组件及比较必须按六后端共同目标验证；不是只跑连接
或单表 SUM。其他方法按冻结的合格 Ibis 或执行前准备→本地实现验证，不要求六套同形 SQL。
若两条路线均登记可用，对同一原始向量做独立 oracle 和双路线对照，分别核验身份/状态/
parts/K；数值相近不证明输入绑定相同。

FixedShape 没有执行后端。保留 Artifact 的原始 backend/profile 作为来源证明，在各
真实 producer 后验证其固定恢复；不能把重复本地计算写成六次源端成功。共用 fixed
证据可以明确复用，但每个 producer 的 schema、receipt、必需 parts 与离线绑定须有实测。

### 2.3 状态、证据和重新资格

| 状态 | 判定 |
| --- | --- |
| planned / unverified | 目标已登记但没有满足该 proof class 的执行证据 |
| blocked | 真实环境、契约或实现依赖缺失；附精确格、原因、owner 和解除条件 |
| failed | 已执行但断言、输出、拒绝期限、提交治理或资源/原子性不满足契约 |
| passed | 本格要求全部实测；若目标是拒绝，明确写为 passed rejection，不授成功路线 |
| skipped | 保留测试运行事实及原因；必需格仍 unverified/blocked，不折算 passed |

每项 proof 分别保存 static、compile、submission、numeric/state、type/domain/parts、
resource/cancel、publication/recovery、cost 和 disclosure 状态，不以一项成功覆盖其余。
预期错误必须匹配 structured expected/received/repair 和实际 I/O/Run 计数，任意异常
或连接失败不算正确拒绝。一个测试可附多个 ID，但每个 ID 有独立断言 owner。

candidate 绑定至少含代码 SHA、dirty diff digest、未跟踪实现/测试摘要、依赖和输入摘要。
修复后记录新候选并重跑受影响正例、拒绝、fault 和续算格；旧失败保留，未受影响证据
只有在 owner/dependency digest 和影响分析可复核时才能复用。最终矩阵须能还原同一交付
候选的资格，不能拼接不相容版本。总数与原始 ID 集合核对，要求 `required = passed +
failed + blocked + unverified`；skipped 是执行属性，不另加进该分母。

## 3. 六后端物理形态与基础义务

下表是 R9.1 展开输入，全部是目标，未在本次执行。每形态分别覆盖 metadata、真实读取、
schema/解码、范围/参数、空输入、完整复合身份、提前停止、失败和资源状态。

| 后端 | 必需物理 profile | 重点正例与反例 |
| --- | --- | --- |
| DuckDB | table、view、CSV、Parquet、local JSON、HTTP JSON；认证/无认证分列 | 文件类型、HTTP scope/重定向边界、精确 Decimal/时间、extension/连接资源；固定 Artifact 不经 DuckDB |
| PostgreSQL | table、view；database/schema/search path 绑定 | 同名不同 namespace、复合身份、Decimal、nullable int64、binary/server cursor、事务与 timezone；不借 catalog SQL 检查业务行 |
| MySQL | InnoDB table、view；声明字符比较政策 | 复合主体键、Decimal mean/div、warning/truncation、无效日期、流式 cursor/提前 close、实际 timezone/timeout；不降为单键或 float |
| SQLite | main 普通 table、view；真实存储类型 | 混合 storage class、int64 溢出、字符串比较、时间解析、增量 fetch/interrupt；datasource SQLite 与 Store SQLite 分开 |
| Trino | Iceberg 与已接受 non-Iceberg connector/table profile 分列 | catalog/schema、Decimal/named row、native/parsed time、分页、取消、源端失败；不将 Iceberg snapshot 语义赋给其他 connector |
| ClickHouse | 本地 MergeTree 与 Distributed 实际 shard 拓扑分列 | Nullable/LowCardinality、Decimal、DateTime64 精度/时区、query id/流式读取/取消；本地节点通过不授 Distributed 或跨表原子快照 |

non-Iceberg 不能只是一个布尔标记，记录 connector、表类型和测试环境；Distributed 记录
shard/replica 数、远端实际读取与失败范围。当前环境未覆盖的部署不获得资格；已冻结
必需形态也不能因无法启动服务而撤销。Replicated、多 worker、其他 catalog、远端对象
存储等未接受拓扑不自动新增承诺，若被当前必需输入引用则登记精确缺口。

整数/浮点/Decimal、native/parsed/calendar time、复合身份、versioned、多根/多 occurrence
按方法适用规则展开。至少涵盖大整数近值、溢出与非有限、声明 precision/scale、无隐式
float、UTC/本地时间与 DST、Duration ticks、完整元组并集而非笛卡尔积。metadata facts
与业务覆盖分别保留 observed/declared/unknown；count/max-time/样本不补业务覆盖。

已知无合法静态路线、mixed、跨 Session/来源或必需 parts 缺失在 Run 与数据读取前拒绝。
必须真实 metadata 才能判断的物理事实只在显式动作/获准执行中读取，不能放入 Logical
构造或纯计划；unknown 不准入。数据依赖重复/fanout/完整性等检查在其消费期限完成，
不能为了“零 I/O 拒绝”伪造事实，也不能发布后才验证。

## 4. 当前实施接缝与补齐原则

| 当前 owner | R9 工作 |
| --- | --- |
| `marivo/datasource/adapters.py`、`engines/*.py`、`backends.py`、`metadata.py`、`capabilities.py` | selected provider、事实/操作资格、session 签发/提交、精确解码、取消及批准语句审计 |
| `analysis/methods/{physical,registry,builtin,semantics}.py` | exact method/type/domain/shape/route 注册、真实 consumer/evidence 与缺口；不另建支持清单 |
| `analysis/compiler/{graph_plan,graph_lowering}.py` 及 graph lowering helpers | 纯计划、所需物理操作、source-prefix、合法后端表达式与检查；不修改编译后的 SQL |
| `analysis/materialization/graph_{source_execution,preparation,exchange,execution,local_execution}.py` | 同一图的源准备、受控批次、检查 deadline、完整本地输入与资源 ownership |
| `analysis/materialization/graph_{publication,storage,store,findings}.py`、`execute_deadline.py` | 原子提交、receipt/parts、Evidence/Findings、600 单调秒预算和不明提交协调 |
| 各方法核与固定视图/codec owner | 只修真实反例；保持方法版本、数值政策、域/状态/parts/K 的既有权威 |
| `tests/multisource_environment/`、共享 fixtures、定向 Runtime 测试 | 复用真实环境准备与只读角色；以公共 typed graph 替换旧 Dataset harness |
| `devtools/analysis_dsl_s3_p3_cost.py`、`analysis_dsl_s4_p4_cost.py` | 只借鉴计数/采样经验；新的成本工具不调用退役私有路线，不继承历史性能结论 |

起草时 `SourceSession.qualify` 对 DuckDB/SQLite 与其余 provider 的操作集合不同；
`methods.builtin` 的多数注册仍是 DuckDB table/Parquet 与 fixed 形态，SQLite 仅有局部
注册。`graph_lowering` 也有特定 DuckDB 表达式位置。R9 必须逐消费者验证这些接缝，不能
只把 backend 名称替换后批量生成 Qualified，也不能仅扩充操作集合宣称 join/union/window
已获资格。该盘点只定位工作，不是前序任务未完成的重新判决。

后端差异由明确注册的物理 implementation/provider 负责，共享 lowering 消费抽象能力；
不在 semantic/core 里新增 backend 分支，不把元数据事实判断复制到 compiler 和 Runtime。
确有新普适规则时先核对 owning contract、版本和回归，不能以 backend hack 隐藏语义改变。
既有 `Unavailable` 和 typed errors 保留 expected/received/具体 repair；无合法路线直接
拒绝，不以终端 SQL、旧 family runtime 或失败后 local 重试补齐。

## 5. SQL 台账闭合与实际提交审计

### 5.1 每条提交必须有来源分类

| 类别 | 合法依据与验收 |
| --- | --- |
| Ibis governed read/check/probe | 绑定表达式、source/purpose/params/schema、session 签发和实际 driver SQL；证明文本未经产品重写 |
| R1.6 provider 固定语句 | operation/backend/purpose 均在已批准范围；关闭注册表、模板快照、typed 参数、逐次提交与凭据脱敏 |
| `md.raw_sql` terminal | 独立公共动作和用户原文；reason/行界/timeout/截断/只读尽力控制；结果不能重入 |
| Store SQLite persistence | 精确 Store 连接及 schema/事务 owner；不能读取 datasource 或执行分析，文件路径不是白名单依据 |
| Test-only administration/oracle | 测试进程拥有 fixture 建库、服务观测和独立 SQL oracle；不导入产品模板、不进入产品执行链 |

DS01–DS22、AN01–AN33 逐项核对最新覆盖及实际调用方；新增发现追加 ID。每行记录原
constructor/caller/submitter、当前去向、替代/删除/批准依据、消费方法/形态、静态扫描与
真实提交证据。历史已删文件不重建，共享 helper 按真实剩余 owner 处理。新链不用的
旧 SQL 生产者/提交入口物理删除并验证不可达，不仅用 run-time guard 隐藏。

扫描覆盖整个 `marivo/`，包括 preflight、metadata、schema、类型/时间校验、source-health、
sample、HTTP、probe、驱动控制、raw cursor、Ibis compiler visitor/SQL AST 后处理；
不得仅 grep SELECT 或只看已知家族文件。以调用图反查签发和 driver 提交，最终每条
生产提交都有类别；未分类或超批准用途立即作为 failed SQL obligation。

### 5.2 三层证据与终端边界

1. 静态扫描和 import/call reachability 钉住残留字符串、入口、模板和编译补丁；字符串
   命中只是线索，按 owner 分类，不能误删 SQL provenance 或合法 Store 事务。
2. 每个实际正例、拒绝及 fault 捕获 session issued read、provider log 与 driver 提交。
   捕获须在查询真正提交的边界，不能仅比较两个都来自 `CompiledRead.sql` 的日志。
   合法 Ibis 自身的 metadata/preparation 提交也逐项标明来源，不默认漏记。
3. 将未知 SQL/篡改编译产物/旧 statement 入口钉死；验证六后端公共操作仍可执行、无
   隐藏预检或失败后回退。环境初始化和服务端独立查询记录在独立测试通道。

R1.6 例外既验证正常事实，也验证未注册 statement、跨 provider/用途、错误参数、事实
unavailable 与 HTTP scope 外不携带凭据。生产日志/索引不保存密码、Authorization、
认证 URL 参数或实际主体键；必要原始测试数据使用可公开的合成身份。

`md.raw_sql` 独立验证原文、空 reason、无效 limit/timeout、`limit+1` 截断、空结果、异常
与 close、真实可执行 timeout、只读权限观测和结果/副本不可绑定 Semantic/Analysis。
timeout 不可强制的格在用户 SQL 提交前拒绝，记录 `query_executed=False`；只读尽力
控制按 owner 披露，不强行承诺所有 backend 都保证。返回行界不等于扫描代价限制。
终端正确拒绝不将该后端的必需 timeout 成功格变为通过，也不授 C04–C14 算法资格。

新的内部 SQL 例外不由本文批准。若必需格确无合法 Ibis/准备路线，先完成反例、可复核
替代研究及最小 operation/backend/purpose 清单，再按具体授权处理；等待决定期间保留
阻塞，可继续独立格。不以既有 provider 例外扩展到 SET/SHOW 时间修正或业务算法。

## 6. 方法矩阵、独立 oracle 与端到端验收

R9.1 将下表与 §2/§3 展开为 requirement IDs。每个适用格覆盖正例、结构化拒绝、批次/
资源、实际提交和恢复；共享 harness 不共享被测算法的预期生成逻辑。

| ID | 方法簇与必需判别 | 独立预期 |
| --- | --- | --- |
| V01 | C01/C02：typed 来源、metadata/认证/参数、Ref/定义/版本绑定、零 I/O 构造、未选驱动隔离 | 原始 schema/物理事实与声明；连接/metadata/业务覆盖分别断言 |
| V02 | C03：完整复合成员键、快照/validity、exact at/before_end、read 四类值 | 枚举原事实；缺版本/重复/跨 Session 拒绝，无隐式 distinct/last-known |
| V03 | C04/C05：多根组件、路径 fanout、完整元组并集、空组、runtime_metric、summarize vs rollup | 原组件 Fraction/Decimal 与当前行统计各自计算；40 vs 130/3 等反例 |
| V04 | C06：四种时间角色、calendar/DST、累计/重叠、半可加 fold 与顺序 | 原边界/认证格枚举；空间/时间归约与 Duration ticks 分开 |
| V05 | C07/C08：Exact/UnionKeys、嵌套 Difference、ratio、状态选择→members、全机会 cohort、固定参照/权重、rank/limit/table | 完整配对/机会域与原参照；Unknown/Undefined/合法零和空域规则 |
| V06 | C09：additive_difference/component_mix、joint/hierarchy、共同 Top-K→Other、筛选核对 | 原 target/basis/components 和独立分配，不以 residual=0 证明语义 |
| V07 | C10：exact distinct/quantile、许可 approximate 与实际方法披露 | 原身份集合与独立排序/线性插值；首次禁止原量 rollup/对应归因 |
| V08 | C11/C12：matching/assignment、重数、漏斗 compare/attribute、completed/observed duration、dropout 与真实 Subject 像 | 独立 occurrence/步骤/覆盖枚举；Journey 重数不被 Subject 去重吞掉 |
| V09 | C13：inception/业务顺序/replay、History、interval/transition/violation、distribution/dwell、时点选人 | 原状态机/边界，不以 occurrence ID 排序授业务顺序；恢复不重放 |
| V10 | C18：elapsed/calendar Anchor、重叠贡献、固定 Subject×Anchor Ω、未知界、any/every | 原 Ω 与覆盖真值，Unknown 留分母；starts-only fixed 缺返程 parts 早拒绝 |
| V11 | C14.a1/a2：zscore/MAD、固定拟合域、全部 Cell、完整格 runs、unavailable、跨批次/DST/真实 Subject | 原整数/Decimal 的中心/尺度与全格分类/最大段；筛选后不重拟合/重分段 |
| V12 | C14.b/c：Pearson/Spearman/Kendall、多量/lag、三预测模型、未来格与名义区间 | 独立配对/平均秩/tau-b、规范模型/创新/方差；保留每 pair/lag 无效状态 |
| V13 | C15：source realization、显式节点共享、fixed hit、发布、Evidence/Findings、receipt/parts/K 冷恢复 | producer/continue/cold 独立进程；旧模型/来源/DuckDB 禁止，原始 digest/域/K 对照 |
| V14 | SQL/路线：实际编译提交、未知入口、无执行后重选、批准例外/终端/Store 隔离 | 真实 driver 边界、提交来源分类、旧入口禁止与原文/快照对照 |
| V15 | 资源/fault：空流 schema、跨批次坏值/键/版本、晚到/close/取消/timeout、事务/ack 不明 | 资源 journal、Run/Artifact/Evidence/Findings 前后计数、服务器 query 状态 |
| V16 | C16/C17 增量：错误/Help/contract/导出/CLI/中英文、optional dependencies、扩展位置 | 独立 reachability/drift/budget/typing，受控能力桩；不授真实 Agent 资格 |
| V17 | 1,000/100,000 与方法压力输入的三路线运行代价 | 原事实摘要、真实计数/字节/采样和原问题结果；详见 §7 |

浮点/Decimal 容差、舍入、有限性和溢出使用既有数值 owner；不为后端测试临时放宽，
不以 float64 统一抹掉 Decimal/大整数差异。oracle 不调用产品 helper、旧 wrapper 或
同一被测数值库计算期望。SQL oracle 只在测试进程独立运行，不成为产品 parity 通道。

公共 A01–A13/J1–J4 按能力和后端适用性绑定脚本、原始 oracle 与进程证据，覆盖完整方法
簇。基础 A01–A04 至少贯穿每个后端的普通表/合法 view 目标；A05–A13 逐涉及方法和
关键物理形态运行，不能仅用一个 DuckDB 大旅程授远端资格。无需全部旅程×所有参数机械
展开；未覆盖的必需格由独立公共最小旅程补齐，而非私有核测试或旧 feasibility probe。

每个新 producer 真实发布后，至少独立继续与断源 cold 进程校验值、Cell、完整域、
RequiredParts、receipt 与可执行 K；固定执行不是只 read/show 已有结果。损坏 part/
schema/version/key/scope/receipt 时 read、execute/exact-hit 必须按契约拒绝。
来源再次 execute 创建新 realization，固定 exact-hit 无核/无新 Run，共享显式节点只
准备一次；分别构造的同形节点不自动合并。

source→Python 的完整准备、核执行和合法后续源消费顺序按同一图检查；固定 Artifact
加入现场图、跨 datasource 现场组合及无凭据回源均拒绝。真实 Agent 由 R10 独立执行，
R9 公共脚本通过只证明技术路径。

### 6.1 资源与取消的可核验边界

每个 provider/profile 覆盖正常穷尽、提前停止、fetch/decode/check/close 失败、连接退出、
主动取消、统一 deadline 和发布前后 fault。使用受控测试数据/query barrier，避免依靠
偶然运行速度；本地注入时钟可验证 600 秒边界，但不能替代真实服务器的 timeout/取消。
已接受预算覆盖准备、本地核、验证和发布前检查，不因进入另一阶段重新开始。

分别记录 `local_closed`、`remote_unknown`、`remote_confirmed`。本地 cursor/reader/连接
关闭不证明远端终止；需要服务器 query id/可观测状态才确认。必需 remote termination
证明缺失时该项保持 unverified/blocked，即使本地清理通过。服务器观测使用独立测试
连接，不新增产品内部 SQL。MySQL drain、Trino 分页、ClickHouse Distributed 远端状态
按各自 owner 单列，不以 coordinator 断连授全部节点终止。

所有失败保持无部分 Artifact/Evidence/Findings 成功；先前成功结果保留。durable commit
后 ack 丢失按 Store 7 协调，不误删已提交状态，也不重复发布。退出进程和 reader 关闭
须能证明资源释放，不以 `finally` 代码存在替代实际观察。

## 7. 运行代价与扩展记录

### 7.1 场景、规模与路线

R9.1 冻结成本 profile IDs；R9.6 执行，不先声称性能改善。六后端普通表基线分别覆盖
1,000/100,000 原始事实、合法 `ibis` 与 `ibis_python` 路线及其真实产物的 fixed-only
续算。额外物理形态在其可能改变准备/传输/取消行为时单列成本 profile；mandatory
路线缺失不能用另一条成功或一个失败的测量点填补。

代表场景覆盖多根 ratio/空组、精确 distinct/quantile、比较选人再观察、joint/Top-K
归因、Event/Lifecycle/Anchor、deviation/runs、多量相关与 forecast，每种实际算法类
都取得成本记录。压力反例包括偏斜分组/大量空组、并列秩/Null、大基数状态、跨批次
长段和 unavailable、较多 occurrence/anchor/lag、完整训练及数值极值；合法参数不被
改成容量准入限制。关联候选 ceiling 等原方法参数界照现有契约验证。

按相同原事实/声明/方法问题比较三路线，source-only 核、Ibis 准备→本地核、纯固定
继续分别标识；来源 capture 和固定核成本不混算，fixed exact-hit 与真正核执行分列。
一次预热与至少三次独立测量，记录冷/热定义、cache/连接复用、批次大小、机器/服务
资源、执行顺序和后台负载；每次来源执行新 realization，不能测历史 definition hit。
原始样本、失败和波动保留；无法完成某压力输入时记录原问题失败/超时及精确格。

The authorized efficiency schedule below supersedes the two-size repetition
matrix for the remaining shared and extra-physical costs only. The six-backend
ordinary baseline retains both sizes and its already completed observations.

### 7.2 必需观测字段与判定

| 字段 | 观测规则 |
| --- | --- |
| 查询次数 | 按业务捕获、check、metadata/probe、provider、终端分列；记录实际提交与显式节点共享，driver fetch 次数不是查询次数 |
| 交换行/列/字节/批次 | schema-bound Arrow nbytes、实际批次数及 driver fetch；若记录网络字节，单独说明测量边界，不能用 Arrow 字节冒充 wire bytes |
| Artifact | 主表/每个 part 的压缩落盘字节、行数/schema/receipt，以及总物化大小；必要 parts 成本不能漏掉 |
| 转换与复制 | Arrow→pandas→NumPy 各阶段、实际 copy/zero-copy 依据及 buffer ownership；view 不能按三份重复求和 |
| 工作区共存 | 阶段采样与峰值 RSS/buffer 生命周期，区分 Python、native、服务端；抽样峰值与精确同时驻留上界分别标明 |
| 耗时 | 单调时钟上的 connect/bind/metadata/compile/prepare/check/kernel/validate/publish/close、总耗时与原始重复样本 |
| 恢复/资源 | fixed/cold 的读取、核与 exact-hit；reader/cursor/连接关闭、服务器终止证据及无法观测项 |

成本工具只作观测，不新增 input-row/decoded-byte/workspace-memory Runtime quota，
不按容量估算拒绝、自动抽样、只取首批、近似或分批重定义全局统计。沿用统一 600 单调
秒执行预算；机器/测试 VM 的部署资源与产品准入分开。Spearman 的完整求秩、MAD 的
完整拟合、forecast 历史和 runs 跨批次状态不能因测量方便缩小输入。

冻结预算来自已经接受的查询共享、精确命中、取消/原子性等结构要求；不杜撰未接受的
绝对速度门槛。所有原结果/状态/身份/K 检查同时通过才构成有效成本样本。可报告观测
增长和瓶颈；只有相同环境、问题与证据边界的 before/after 才支持局部性能比较。旧链
语义不同或已退出时仅保留历史基线，不宣称通用加速或内存降低。

### 7.3 扩展改动位置

记录本次每个 backend/物理形态需要修改的 adapter、implementation 注册、表达式、解码、
资源、测试和披露文件及原因，给出依赖/提交位置。用受控能力桩验证更换 provider 不需
改变业务核、未选 optional driver 不被导入、无合法实现和错误 schema 拒绝且不回退；
桩不是第七后端或真实环境资格。合理 core 规则改动须说明普适契约与回归，不能为了
“零内核修改”藏在 adapter。类/文件数量下降与一次速度不能代替扩展证据。

## 8. 工作包顺序与独立出口

每包完成自己的实现、定向验证和证据，再启用依赖它的包；部分 backend 阻塞时可推进
已获前提的独立格，不能提前宣布该包或 R9 全体完成。

| 工作包 | 实施与主要文件责任 | 独立出口 |
| --- | --- | --- |
| R9.1 交接与矩阵冻结 | 对齐 R0/各阶段账本与 R8 种子；冻结 candidate、requirements、profile/oracle/命令/owner、成本与 SQL ID；建立测试收集/证据核验入口 | 原始目标全映射，适用/拒绝/阻塞分清，分母/依赖/hash 可重建；static 不记 Runtime passed |
| R9.2 真实环境与基础读取 | 复用 multisource 环境，绑定版本/只读角色/拓扑；adapter/provider/source/schema/metadata/认证/控制、资源与 optional dependencies | 六后端逐物理 profile 的基础正反例、实际提交及资源证据；smoke 不授 Analysis method |
| R9.3 方法物理实现补齐 | method registry、provider 物理操作、graph lowering/preparation、精确解码及必要 local 消费；按确认反例修复 | 每必需方法键有合法已连接 consumer 或精确阻塞；已通过格含真实执行、oracle/检查，无批量 Qualified/编译补丁/fallback |
| R9.4 完整图、固定恢复与故障 | 公共 V01–V13/V15 组合、各 producer 离线 continue/cold、parts/K、真实取消/timeout、Store/Evidence/Findings fault | 适用必需格有源/固定/冷与拒绝期限证据，所有资源/原子性义务独立闭合，未验证不代授 |
| R9.5 SQL 台账与残留退出 | DS/AN 全库反查、driver 审计、provider/终端/Store 分类；删除确认被替换的生产入口/模板 | 未批准 SQL 与未知提交为零；批准例外有精确依据/快照/用途证据，旧路径不可达且物理退役 |
| R9.6 成本与扩展验收 | 新公共成本 harness、原始样本/摘要；1k/100k/压力三路线、实际转换/共存与扩展文件位置 | V17 成本集合完整、测量可复现、结构要求/结果保持、失败独立；不作无依据性能声明或容量门禁 |
| R9.7 整体验收与 R10 交接 | 冻结 ID 总审计、影响重跑、compact broad gate、必要 Runtime/site、披露/证据索引、remaining work | 必需格全 passed 且候选一致，SQL/成本/扩展出口闭合；R10 独立 wheel/真实 Agent/发布输入齐全 |

R9.3 可按后端逐个闭合，但共享 registry/Runtime 修复须先验证其他已通过后端的受影响
格。R9.5 的扫描/提交审计从 R9.2 起运行，R9.6 的计数从 R9.3 起接入；工作包顺序不是
等到最后才发现 SQL 或成本问题。补齐缺实现属于 R9；改变方法语义或扩大已批准 SQL
用途先回到 owning contract/明确授权，不能在资格测试中自行改定义。

### 8.1 拟新增产物与已有 harness 的使用

R9.1 创建以下产物，本文不提前创建通过账本：

- `docs/superpowers/specs/2026-10-04-marivo-full-algebra-dsl-r9-qualification-ledger.md`
  与机器可读 requirements/result 索引：原始集合、方法/形态/路线、proof 和状态。
- `docs/superpowers/specs/2026-10-04-marivo-r9-evidence-index.md` 及独立 hash 的证据包：
  environment/candidate/oracle/actual submissions/resources/recovery/cost/扩展位置。
- `tests/test_full_algebra_backend_matrix.py` 与必要共享 fixture/worker：承接 R0 计划的
  公共 backend matrix；拒绝、SQL 和进程证据按责任拆分，默认测试不启动服务。
- `scripts/r9_qualification_requirements.py`：从冻结输入生成 ID、校验缺失/重复/孤立附件和
  proof 绑定，不凭测试总数生成通过。`devtools/analysis_r9_cost.py` 留给 R9.6：
  R9.1 只冻结成本场景，不提前实现测量工具。

R9.1 静态文件与 CLI 已落地，精确入口见 evidence index；后续 Runtime 节点、worker 与
成本工具仍由各自工作包绑定，不得将规划命令记成已执行。
已有 `test_r12_source_adapters_runtime`、`test_r13_control_boundaries`、
`test_datasource_r16_regressions`、`test_datasource_provider_capabilities` 可提供当前基础
回归；`test_lazy_*`、`multisource_environment/qualify.py` 中旧 compiler/snapshot probes
只提供反例，不将其 SQL AST 扩展或旧 Dataset 调用搬入新 graph 产品链。

## 9. 工程门禁、证据与完成判定

### 9.1 执行入口与验证层级

实施前读取 `tests/multisource_environment/README.md` 与 `manage.sh` 的当前生命周期。
真实服务使用专属环境和 UUID fixtures、只读产品账号、finally 清理；保留不属于本任务
的服务、数据库、卷和本地改动。现有 start profile 有互停行为，按真实依赖安排串行
执行并记录服务状态；不把服务已启动当作 ready，不由 pytest/default gate 自动启动。
凭据只用 env 引用和私有测试配置，不输出 Compose resolved config 或账号秘密。

以下是当前存在的定向回归命令，仅说明实施入口，本次未运行：

```bash
make test TESTS='tests/test_datasource_adapter_contract.py tests/test_datasource_provider_capabilities.py tests/test_datasource_r16_regressions.py'
make runtime-test TESTS='tests/test_r12_source_adapters_runtime.py tests/test_r13_control_boundaries.py' RUNTIME_WORKERS=1
```

远端命令须按已 ready 的服务显式启用对应 `MARIVO_*_ANALYSIS_TEST=1`；Distributed 使用
既有 `MARIVO_CLICKHOUSE_CLUSTER_TEST=1`。未启用或无法连接保留 skip/阻塞，不能以上述
命令退出 0 授六后端资格。新 matrix 落地后逐精确 ID/backend/profile 运行，例如以下
**拟用命令**；`C03a` 是测试 selector 规划，落地后以实际 collection 为准：

```bash
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_full_algebra_backend_matrix.py -k C03a' RUNTIME_WORKERS=1
make typecheck TYPECHECK_TARGETS='marivo/datasource marivo/analysis/methods marivo/analysis/compiler marivo/analysis/materialization tests/typing'
make lint-agent LINT_TARGETS='marivo/datasource marivo/analysis tests/test_full_algebra_backend_matrix.py'
```

新 fixture/test 修改使用仓库 `marivo-test-fixtures` skill。Python 使用 `.venv/bin/...`
或 Make entrypoint。验收按下述分层流程执行，不为每个小增量重复整套门禁。

### 9.1.1 后续增量验收与去重

| 阶段 | 必需执行 | 不再重复执行 |
| --- | --- | --- |
| 修复迭代 | 最窄失败节点及直接受影响的正例/拒绝；必要时 touched-module lint/typecheck | 无相关行为变化的后端、全部历史场景、完整 broad gate |
| 业务实现收口 | 合并同一工作包的变更；一次受影响 Runtime 批次，按 backend/profile 分组 | 每加一个向量就重跑已通过批次；在 Trino/ClickHouse 间来回切服务 |
| 证据绑定 | 从已有原始执行包校验实际提交、oracle、parts、取消及哈希；运行受影响 binder 拒绝测试 | 只因 binder、索引、证据打包或披露文案变化重跑业务查询 |
| R9.3 最终候选门禁 | 一次 `make check-agent`；latest site 有改动时另执行一次 site build | 每个 Cxx、backend、数值类型或证据目录单独跑全量门禁；同一候选再单独执行 full lint、full typecheck、full default tests、API docs build |

同一候选、同一 backend/profile 和相同 Runtime 路径的共享取消/释放/原子性证明只采集
一次，由多个适用 requirement 引用同一附件。pending、initial-response、fetch 等不同
取消阶段，以及完整键、Null、版本边界等不同风险仍是独立证据，不能合并成一个 smoke。
统计工作包的 `--all-statistical-backends` 仅执行 retained numeric、source statistic 和
source deadline 消费者；C04/C08/C09、cohort、cold continuation 和独立 ratio 回归从该
入口移除，由所属工作包或本次变更的影响范围执行，不再作为每轮统计验收的附带项。

冻结放在业务实现与消费者稳定后；正式采集期间不改候选。绑定器先利用已有执行包调试，
待绑定规则稳定后再批量收口。旧包保持其原 candidate/owner/hash，证据整理不复制授予到
新候选；最终同一候选验收按影响范围补齐，不在每个中间编辑后重建全部历史通过项。
已通过检查只在新行为变更、失败或未解决疑点时重跑；重跑需说明受影响的节点和原因。

日常执行只保留三步：确定本次变更的受影响节点；在当前已 ready 的后端合并运行这些
节点并保存原始证据；更新绑定和剩余任务。C04 的多个后端和数值类型属于同一能力
收口，不因新增开发证据目录触发一次全量门禁。只有共享编译、解码、生命周期等行为
变化才扩展到其他使用者；单独增加 oracle 断言只验证该节点。正式统计入口不用于
增量调试，增量直接使用 `make runtime-test TESTS='实际文件或节点与 selector'`。
验收脚本最多报告五个失败后停止，修复已知问题后再运行受影响批次。

完整 Runtime、固定恢复、fault、成本和 SQL 退出由各自工作包承担；R9.3 不重复执行
R9.4–R9.7 的全量验收。required IDs 和独立风险分母保持可追踪，删去的是重复执行要求。
最终 R9.7 仍须完成 broad gate 与真实 matrix，默认测试省略的 Runtime 不算通过。
API 文档包含在 compact broad gate；latest 中英同时更新。

R9 不运行 release-check 来代替这套真实矩阵，也不为普通门禁启动 MinIO。最终同一候选
隔离 wheel、installed multisource gate 和真实 Agent 交由 R10；如 R9 独立取得 package
证据，仅标具体范围，不将源码测试写成已安装包成功。packaged skills 修改仍按 AGENTS.md
需要明确授权，本文不包含编辑授权；披露修复如涉及该项，记录具体所需变更后处理。

### 9.2 证据包与披露同步

最终同候选资格的每个 ID 至少保存 candidate/owner/contract/environment/input digest、精确命令/节点、
开始结束时间/退出码、独立 oracle、实际提交来源/批次、检查/资源/取消、结果/parts/receipt/
K、附件 hash 和状态。成本还含测量边界与原始重复样本。无日志、失败日志或只引用
不可取得的 `/tmp` 路径不能构成最终唯一通过证据。

R9.3 实现收口按 §8 的独立出口判定，不提前执行 R9.7 的同候选资格门槛。已有原生
consumer 可在原始开发包保留的环境、输入、实际提交、独立 oracle、parts/checks 和
日志完整可取时复用；同时核对当前精确物理键仍连接合法 consumer，引用适用的共享
资源证明，并保存当前 owner 摘要。原始 candidate、命令和时间仅按已有记录保留，
缺项明确标未记录，不补造或改写。此类记录授予的只是具体 representative 的 R9.3
实现完成；不得写入同候选资格索引或隐含 R9.4/R9.7 完成。仅缺最终资格记录格式时，
将该缺项移交 R9.7，不因整理文档、candidate 或附件重复已通过的业务测试。实际缺失
的 oracle、执行、parts、独立风险或资源义务仍须补齐，不能用 static 注册替代执行。

保留小型可移植索引与合成 oracle；大型日志/轮子/矩阵按仓库文件限制拆成独立 hash
shards，验证有序重建摘要。qualification JSON 可能被现有 ignore 规则排除，直接校验
文件与交付位置；不修改 ignore 以整理前序状态，也不绕过 added-large-files hook。
证据包完整性证明不是方法资格，分开记账。

后端能力或 typed repair 变化视为 disclosure-contract 变化，同一改动更新 owning specs、
原生 Help/预算、动态 contract/error、独立 drift/reachability/typing 测试、API/CLI 和
latest 中英例子。测试 registry snapshot 不当作 runtime 事实，renderer 不维护另一份
支持矩阵。只改受影响事实，公共入口和参数仍由自然 owner 持有。

### 9.3 R9 完成判定与 R10 交接

以下条件全部满足，才将 R9 标为完成：

1. R0 与 R1–R8 的运行相关必需目标、R8 profile 种子全部有映射，原分母和新增项可重建；
   每个必需成功/拒绝 ID 有匹配的真实证据，failed/blocked/unverified 为零。
2. 六后端及冻结物理形态分别满足方法、类型/时间、完整域、检查、批次/资源/取消；
   编译、metadata、smoke、旧测试或 skip 不代授资格。
3. 每个 producer 的固定继续、exact-hit、断源新进程、receipt/parts/K 与故障原子性有
   实测；没有 Artifact→DuckDB、显式 mixed、跨源上传、来源失败后重选或旧 Runtime。
4. DS/AN 台账、全库静态反查和实际 driver 审计闭合，未批准 SQL/未知提交为零；
   R1.6、公共终端与 Store 事务分别有严格范围和证据，无新增未经批准例外。
5. 按 §7 最新授权范围完成成本记录：六后端普通基线保留 1,000/100,000；已完成的
   Distributed 100,000 实测保留，后续方法压力与物理形态改用 1,000 正式成本。三类路线、共享覆盖、
   规模豁免及转换/共存/物化和扩展改动可重建；结果/状态/身份保持，未增加容量准入，
   性能结论与实际观测范围一致。
6. 受影响类型/错误/Help/contract/CLI/site 对齐，`make check-agent`、定向真实 Runtime
   与必要 site 构建通过，修复影响已重跑并绑定同一交付候选。
7. R10 收到精确 candidate/依赖/矩阵/环境、可执行公共旅程与 oracle、冷恢复/fault、SQL
   分类及成本/扩展索引；最终安装包、真实 Agent、全库收口和发布未被 R9 的成功隐含完成。

部分环境不可用时提供已通过集合、精确阻塞和解除动作，不宣布 full R9 完成。R10 可
读取交接并准备独立材料，不能越过 R9 未闭合的资格来宣布全重构或发布接受。

### Authorized Duration Unknown quotient extension (2026-10-06)

The user's choice 2 authorizes the existing retained Duration quotient to
propagate method-owned Unknown coverage/entry Cells as scalar float64 Unknown.
Self-division retains uncertainty and both ordered endpoint Cells. Defined zero
denominators retain Undefined(zero_denominator). The implementation uses an
explicit Duration-only value policy and validates retained endpoint consistency.
The concrete producer is the fixed microsecond Journey observed_duration ratio.
Its fixed NoTime float64 zscore/MAD and Pearson/Spearman/Kendall consumers are
qualified only with actual public execution; forecast/runs still require an
independently proved complete time grid. This authorization grants no new
History comparison endpoint, datasource control SQL or snapshot authority.


### R9.3 input-read consistency amendment (2026-10-06)

The user permits source changes across successive native reads and explicitly
rejects introducing domain snapshots. PostgreSQL, MySQL, Trino and ClickHouse
preparation therefore retains an `independent_reads` authority for the actual
inputs. It must not start a repeatable-read transaction or claim a shared source
revision. Retained inputs remain reusable and source-free after acquisition.
Snapshot consistency is removed from the sixteen remote C11/C12/C13/C18
acceptance prerequisites; exact connected consumers, keys, multiplicity,
coverage, cancellation and resource release still require evidence. The earlier
PostgreSQL transaction-control approval is superseded and its two registrations
are withdrawn. This change neither grants physical keys nor marks scenarios passed.

### R9.6 weak source-check consistency amendment (2026-10-06)

The user accepts independent source versions for checks and subsequent native
calculations, without a new snapshot requirement. Check evidence proves its own
query only; it is not a same-acquisition certificate for a later calculation.
Logical node sharing and one Run/result identity do not guarantee one physical
scan. Qualified pure-native graphs keep dependencies as Ibis expressions and
query checks, required parts and the terminal primary directly, without staging
an intermediate Arrow capture back into literal or temporary source relations.
Independent checks may still produce temporary Arrow validation results; the
eliminated work is intermediate stage capture/restaging, not check-result reads.

Final Arrow schema/key/Cell and primary/RequiredParts/internal-arithmetic checks,
atomic Store 7 publication, read-only permissions, unchanged compiled submissions,
the shared 600-second deadline, cancellation and resource release remain required.
Hybrid/local graphs still prepare every source dependency before local selection;
fixed continuation remains source-free. No fallback or new SQL exception is
authorized. The owning contracts are the [analysis design](../../specs/analysis/python-analysis-design.md#r96-source-check-consistency-amendment-2026-10-06)
and [Runtime](../../specs/analysis/session-state-and-runtime.md#r96-direct-native-expression-execution-2026-10-06).
Earlier cost samples retain their original execution candidates and scoped or
historical authority; this refactor does not grant current six-backend or full R9.6
qualification, a new reuse proof, or a before/after performance conclusion.

### R9.6 shared local preparation optimization (2026-10-06)

The user approved bounded bulk Arrow complete-key/Cell decoding and reuse of the
same stream's completed primary key index within collection, plus elimination of
repeated selected-row, key/Subject and window/grid expansion in prepared
observation. These are shared internal paths, not backend-specific SQL changes.
Every complete-key row retains its deadline checkpoint, cross-batch uniqueness
and nullable policy. Exhaustion and successful close are required before index
reuse. Independent RequiredParts and method-state validation, exact numeric
state algorithms, original envelopes, grid membership and overlapping
contribution uses remain unchanged. No result cache spans invocations or source
reads. Empty selection and invalid-input error precedence remain unchanged.

The pre-optimization cost checkpoint, including failures, retains its original
candidate. New performance evidence binds the changed candidate and the actual
deployment separately; no existing reuse proof implicitly grants the optimized
implementation. Validate the affected private contracts and one bounded Runtime
integration batch, then one compact broad gate and representative 100k local/fixed
measurements. Do not repeat unaffected successful backend groups merely to
rebuild evidence. Remaining ordinary, physical and shared cost obligations stay
open until their own evidence is complete.

### R9.6 efficiency-first cost amendment (2026-10-06)

The user explicitly selected the 42-group efficiency schedule. This amendment
changes remaining cost repetitions and evidence reuse, not method qualification,
public algorithms, original requirement IDs or source/fixed route boundaries.
The six ordinary backends retain their 72 completed baseline groups at both
1,000 and 100,000 facts, including separately witnessed cold continuation.

The remaining formal schedule uses 100,000 original facts, one warmup and three
independent measured realizations per applicable group:

| Remaining scope | Source | Fixed kernel | Exact-hit | Total |
| --- | ---: | ---: | ---: | ---: |
| CSV, Parquet, local JSON, Trino non-Iceberg and ClickHouse Distributed | 10 | shared baseline evidence | shared baseline evidence | 10 |
| Twelve representative and pressure recipes | 12 | 10 | 10 | 32 |

The many-occurrences event recipe covers the basic event recipe's complete
Journey, Lifecycle, Anchor and elapsed outputs. The full-training numeric-extreme
forecast recipe covers all three ordinary forecast models and their complete
training window with its own oracle. Each original scenario ID remains present
with an explicit covering recipe; old weaker extreme samples are not upgraded.
Dense ties/Null ranking, ordinal high-cardinality ranking, explicit empty groups,
multi-root ratio, exact distinct/quantile, comparison/selection, joint/Top-K,
zscore/MAD, cross-batch long runs/Unknown, all association algorithms and all
forecast models remain actual consumers with complete output checks.

At 1,000 facts each new applicable shape receives one functional realization:
32 shared source/kernel/hit records and ten physical source records. These
42 records are functional probes, not warmup/three-repeat cost cohorts, and do
not support per-scenario or per-profile 1k-to-100k performance growth claims.
The 100k exact-hit representatives retain full input/output and RequiredParts
validation; exact-hit is not treated as constant-time metadata lookup.

Physical FixedShape costs may reference an already measured equivalent ordinary
baseline only after that actual producer's schema, carrier, receipt, required
parts and source-offline binding are verified. Reuse does not claim that the
physical profile itself obtained the referenced timing. No additional cold
costs are required here; independently owned recovery proofs remain mandatory.
Both HTTP profiles retain their original positions and exact refusal witnesses.

All original IDs, historical failures and execution candidates remain intact.
Every omitted two-size repetition has an explicit shared-evidence or authorized
scale-waiver mapping; it is not relabeled as an unexecuted measured pass. The
600-second deadline, complete facts/keys/Cells/parts, independent oracles,
read-only submission governance, cancellation and atomic publication remain
unchanged. This amendment authorizes no input quotas, approximation, source
fallback, reduced training/grid/occurrence inputs or full R9 completion grant.

### R9.6 remaining 1k acceptance amendment (2026-10-06)

The user's later instruction supersedes the still-open executions of the
42-group schedule above: use 1,000 original facts for the forty remaining
groups, with one warmup and three independent measured realizations per group.
The two completed ClickHouse Distributed 100k formal groups, their two actual
1k functional probes and their actual 100k producer binding retain their
original execution candidate and observations. They are not recollected.

The remaining schedule is `r96-remaining-1k-v1`: eight source groups for CSV,
Parquet, local JSON and Trino non-Iceberg, plus the same 32 shared
source/kernel/exact-hit groups. There are no additional separate functional
probes or cold repetitions. Each formal 1k record executes the unchanged full
independent oracle. The four remaining physical producer bindings execute at
1k and cannot grant an unexecuted 100k physical producer.

The remaining 100k executions and new 1k-to-100k growth claims are explicitly
waived, not measured passes. Original IDs and the earlier coverage map remain
traceable to this authorized bounded scope. Ordinary baseline timings keep
their original scales; Distributed and remaining medians are never pooled
across scales. Shared aliases and exact fixed reuse still require their actual
complete outputs and equivalent producer/parts/source-offline proofs.

All recipes, complete training/occurrences, keys, Cells, RequiredParts,
independent original-key oracles, the 600-second deadline, read-only/resource/
cancel and atomic-publication rules remain unchanged. In particular, the
cross-batch long-runs recipe keeps its complete 1,536-period grid and actual
multi-batch witness; reducing original facts does not shorten that grid.
Historical failures keep their actual precision, candidate and deployment.
This is a cost acceptance-scale amendment, not a new product qualification,
capacity limit, approximation, or full R9 completion grant.
