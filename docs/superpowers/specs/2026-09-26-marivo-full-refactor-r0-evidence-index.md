# R0.1 工作基线与历史证据索引

Date: 2026-09-26

Status: R0.1 历史边界登记。本轮未运行产品测试、远端后端、安装包或真实 Agent；历史通过只属于当时的代码、依赖、输入和路线。

依据：[R0 实施文档](archive/2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md)。文件级 SHA-256、字节数和可取得性在 [manifest.json](evidence/r01/manifest.json)。

## 1. 隔离工作基线

| 项 | 原 checkout | R0.1 checkout |
| --- | --- | --- |
| 路径 | /Users/lichengxiang/source/oss/marivo | /Users/lichengxiang/source/oss/marivo-r01-evidence |
| 起步 HEAD | 95cb4ecff8126362a409246acebc2b223966a3d3 | 同一提交 |
| 分支 | panda | codex/full-algebra-r01，经 git worktree add -b 建立 |
| 跟踪文件 diff | 空；git diff --binary HEAD 的 SHA-256 为 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 | 建立时为空 |
| 未提交路径 | 仅下面两份未跟踪计划 | 两份计划按原字节复制，未移动或覆盖原件 |

原 checkout 的未跟踪快照按路径字典序，将 path + NUL + file_sha256 + LF 连接后做 SHA-256，结果为 c0b0f0ef51b4e634ef010f996f6fe5d2505f49cbf36b87d28b1240cfd44e6852。它与空的跟踪 diff 分开记录。

| 输入 | SHA-256 | 起步状态 |
| --- | --- | --- |
| [代数 v0.5](archive/2026-09-23-analysis-algebra-theory.md) | c1901f306952e294fb11c329fbf66f5358e1ec3e605f69d9dcd3e60852acc309 | 已跟踪 |
| [DSL 接口设计](archive/2026-09-24-marivo-semantic-analysis-dsl-interface-design.md) | 319223cf12e7c9b53b0b5bda5c0d7b2e558b4e372e48534e072eb53eed39365a | 已跟踪 |
| [架构设计](archive/2026-09-24-marivo-analysis-dsl-architecture-design.md) | 1f1feab98bf9c91a86b23bae83ef7e5bb764658141866b943d89611a24ffb001 | 已跟踪 |
| [MVP 验证计划](archive/2026-09-24-marivo-analysis-dsl-mvp-validation-plan.md) | 83d887968bf602a0674dc2903681ec81d828b8e7985679e66c2f68b3560b3cd5 | 已跟踪；历史验证参照 |
| [全量主计划](archive/2026-09-26-marivo-full-algebra-dsl-refactor-implementation-plan.md) | 2c433063e7275e33481cf9fd33be28d32ea5236c6167d6484ee14b2239f93646 | 原 checkout 未跟踪；已复制 |
| [R0 实施文档](archive/2026-09-26-marivo-full-algebra-dsl-r0-implementation-plan.md) | fd077522e35edc3450bfe6728544d5166c9002d3b8502e80ffdb4f255e382c72 | 原 checkout 未跟踪；已复制 |

接续阶段须记录新的代码 SHA、diff hash 和输入 hash；变化时重新核对受影响单元。尚未提交的 checkout 文件不能称为远端已取得。

本轮中途，原 panda checkout 快进至 72001598a76ea6e23af2fb8c3f438ecc78d22b13，
将主计划以相同 SHA-256 纳入 Git，并补充成员身份、Subjects 集合像和有依据的单射规则。
隔离分支已在未编辑原工作树的前提下快进到同一提交。变更仅是目标契约澄清，
不改变本索引的历史运行事实；R0.2 的成员能力行必须按新规则盘点。
快进后四份设计输入的 SHA-256 依次为：代数
40ce44e730a245b9a9dad50ac6fe64effa5c374960ef658ec9eaaa328f6a2e14、
接口 d31aa12ac4f4bffe571572e7824a7d2f33f83f6e8a372817905c5dd30ab5e4ad、
架构 1c8bf9f9ccae7961b165b6ace061b721962bd634489a9e6dd95d0909e4d074b5、
MVP 83d887968bf602a0674dc2903681ec81d828b8e7985679e66c2f68b3560b3cd5。
上表保留原始起步快照，manifest 记录快进后的 checkout 内容。

## 2. 可取得性与原始附件

原 docs/superpowers/plans/ 被 .gitignore:97 忽略。原 S0 与组合验收正文已逐字节复制为 [S0 归档](evidence/r01/s0-acceptance.md)和[组合验收归档](evidence/r01/composition-acceptance.md)；S4 P4 的 installation、projects、逐题 oracle/user/recovery JSON 已复制到 [P4 机器结果](evidence/r01/s4-p4/)。归档保留原历史结论；原文相对链接仍相对旧 plans 路径，复核代码请从本索引定位。

manifest 登记 218 个文件，其中 165 个 S4 Agent 原始/审计文件仍只在原 checkout 的忽略目录。成功和失败尝试的 trace.jsonl、answer.py、audit.json、attempt.json、prompt.txt、stderr.txt、replay.json 保持原样，逐份记录 hash。它们在当前机器可读，另一个 checkout 不能仅凭 Git 获得。可移植原始证据需要另作受控归档与敏感信息检查；否则在新隔离项目、新会话按原入口重跑。/tmp wheel 也仅本机可得。缺原始附件的资格一律为「历史记录，待重跑」；脚本、mock、/health、编译或静态读取不能补成真实执行或 Agent 证据。

## 3. MVP、Runtime、wheel 与 Agent 的历史层次

| 层 | 代码与依赖锚点 | 当时事实、独立预期及边界 |
| --- | --- | --- |
| S0 私有契约 | fb827a95 起步、a4757f2b 完成；macOS/Python 3.12.13、Ibis 12.0.0、DuckDB 1.5.3、pandas 2.3.3、PyArrow 25.0.1 | [S0 §1–§2](evidence/r01/s0-acceptance.md)：44 项私有契约/fixture、119 项旧回归、默认门禁 5067 passed/4 skipped。J1–J4 的 SQL/算术只验证 fixture；当时公开 DSL、真实执行与 Agent 未验证。终端 stdout 原文未归档 |
| S1–S3 私有 J1–J4 | [组合验收 §1–§14](evidence/r01/composition-acceptance.md)逐节各有代码锚点；S3 P4 为 5766bdc7；Python 3.12.13、Ibis 12.0.0、DuckDB 1.5.3 | J1 Ibis/DuckDB 和固定续算，J2 严格下降域，J3 多根原状态 ratio，J4 Python/来源 Spearman；S3 集成 216 默认、18 Runtime。独立 SQL/Fraction/平均秩在 tests/test_analysis_dsl_fixtures.py。私有 execute_j1 不证明新公开 DSL |
| S4 P3 安装包用户脚本 | 起步 c23b3b6；最终 wheel SHA-256 6f466cab568788740224d05492af6db4b266ea3f1e91f619c5b439115d5388e6；Python 3.12.13、Ibis 12.0.0、DuckDB 1.5.5、pandas 2.3.3、PyArrow 25.0.1、SciPy 1.18.1、NumPy 2.5.3 | [组合验收 §15](evidence/r01/composition-acceptance.md)：独立控制器按原始 DuckDB 事实以 SQL/平均秩求 oracle，并断源冷恢复。J1=1000，J2=A/C 与 15，J3=40 与 130/3，J4 Spearman=-0.4。原 s4-p3-evidence-* 和 wheel 本机可得、不随 Git |
| S4 P3 真实 Agent | 尝试所用 wheel 逐次见组合验收 §15；最终 J1/J2/J4 为 axisfinal wheel，J3 为上述 axisverified wheel；Claude CLI 2.1.186 实际模型 zai-messages/glm-5.3-flashx | J1 axisfinal/agent/j1/attempt-06、J2 attempt-06、J4 attempt-05、J3 axisverified-retry/agent/j3/attempt-07 的历史审计与同 wheel 重放通过。首次错误时间域、旧 Dataset 绕行、硬编码答案和 J3 重试保留为失败史。完整轨迹仅本机忽略目录可得；审计零越界不代替人工复核，更不能迁移为新 DSL 通过 |
| S4 P4 公共 Runtime/安装包 | 5df3414e2d34727a0b464f1928b51391ee571388；wheel SHA-256 48b393c3accce8a20c0fe303941d5ec85e9c627e851c46d38821cabd40df95ee；同 P3 依赖族 | [组合验收 §16](evidence/r01/composition-acceptance.md)与[P4 机器结果](evidence/r01/s4-p4/)：四题安装包脚本、独立 oracle、冷恢复，公共 Runtime 18 passed，默认门禁 5165 passed/4 skipped。扩大整个旧公共测试文件的 mypy 检查另有 9 条历史诊断。P4 未重跑 Agent、远端或发布 |

本轮重新核对了本机 P3 最终与 P4 wheel 文件 hash，未安装或执行。P4 installation.json 指向 site-packages 的 wheel URL，但 direct_url 记录中没有 archive hash；验收正文与现存文件提供上表 hash。复跑入口为 devtools/analysis_dsl_s4_p3/README.md 与 controller.py prepare/scripts；独立预期由 devtools/analysis_dsl_s4_p3/oracle.py 从事实取得。技术回归历史命令为 make runtime-test TESTS='tests/test_analysis_dsl_public.py'，本轮未运行。

## 4. C0–C10 后端历史证据

下表所有验收正文在 docs/superpowers/specs/ 受 Git 跟踪，hash 在 manifest。代码锚点明确区分实施起点和验收代码；不能把当时未提交实现归于起点。仅 C3a、C10 有相应已跟踪机器回执；其他行的原始终端输出未移入本证据包。C3 分为 C3a/C3b 两份记录。

| 包 | 验收正文与代码锚点 | 历史事实和未闭合格子 |
| --- | --- | --- |
| C0 | [基线](2026-09-16-multisource-capability-c0-acceptance.md)；起点 7ddbe940 | K/H/W/L/U 分类的只读基线与目标矩阵，不是 C1–C10 运行通过 |
| C1 | [必要列](2026-09-16-multisource-capability-c1-acceptance.md)；起点 7ddbe940，当时实现未提交 | 六后端必要列、schema/权限旅程，限记录中的类型与方法 |
| C2 | [标量类型](2026-09-16-multisource-capability-c2-acceptance.md)；起点 2602989a，当时实现未提交 | 六后端 Boolean/UInt/string/timestamp 各自物理范围；时间轴/DST 属 C3 |
| C3a | [原生时间轴](2026-09-18-multisource-capability-c3a-acceptance.md)、[机器回执](2026-09-18-multisource-capability-c3a-execution-receipts.json)；起点 d0822098 | 六后端已准入原生时间与单单位桶；非 Iceberg、非 UTC 等受物理门约束 |
| C3b | [解析轴/多单位桶](2026-09-19-multisource-capability-c3b-acceptance.md)；起点 facd67b0、验收代码 6fb4052e | DuckDB/SQLite/MySQL/PostgreSQL/ClickHouse 限定通过；Trino 仅生成级且服务不可达，未验收 |
| C4 | [计算量/Decimal](2026-09-20-multisource-capability-c4-acceptance.md)；起点 62ccfd25、验收代码 04391c78 | 六后端受限行表达式/Linear；Decimal 逐格开放，MySQL mean/div 与 Trino Decimal 保留拒绝 |
| C5 | [表形态](2026-09-21-multisource-capability-c5-acceptance.md)；起点 33c04ca6、验收代码 11ca608a | 视图、Trino non-Iceberg、ClickHouse Distributed 等按实际表形态限定 |
| C6 | [时间状态](2026-09-21-multisource-capability-c6-acceptance.md)；起点 79a9aaff、验收代码 8946cd71 | 五远端日历/累计/fold 分格验收；MySQL first/last 等仍拒绝 |
| C7 | [distinct/分布](2026-09-22-multisource-capability-c7-acceptance.md)；正文未给单一最终代码 SHA，实施当时未提交 | 五远端 exact distinct/linear distribution；C10 后续只抽样，不补成全量 wheel 矩阵 |
| C8 | [高级方法](2026-09-22-multisource-capability-c8-acceptance.md)；起点 ff9a4ad0 | 五远端 Entity correlation；Candidate/Driver 关闭，部分 attribution/Top-K 受限；审查时 Docker 不可用，未重跑所有远端 |
| C9 | [Event/Lifecycle](2026-09-22-multisource-capability-c9-acceptance.md)；初始起点 62082259、续做起点 0ba8e451 | PostgreSQL/Trino/ClickHouse 特定正向；SQLite/MySQL 与部分派生方法未开放。MySQL 实验容器 exit 137 后撤回，失败不得改成通过 |
| C10 | [安装包](2026-09-23-multisource-capability-c10-acceptance.md)、[机器回执](2026-09-23-multisource-capability-c10-execution-receipts.json)；起点 d3aa5b94 | wheel SHA-256 5e52c10c3625b82f2e6853bc58f1fa4b67200d1ca7a8f4829b53fc0673d41b83 六 profile 有界样本；未逐格重跑 C1–C9，ClickHouse Top-K 有 code 241 资源失败 |

C0–C10 的依赖、服务、reader 权限、表类型和复跑命令以各正文及回执为准，不能用 2026-09-26 当前 .venv 覆盖历史版本。C10 入口包括 make pypi-build pypi-check 和记录的各安装 profile；远端服务须按原 runbook 准备。R0.5 才从这些正反例构建新 DSL 的方法 × 类型 × 时间/来源 × 后端/表 × 路线矩阵；此处不转授新资格。

## 5. 可复用的独立 oracle、故障与业务问题

| 用途 | 输入与独立预期 | 与旧执行产物的隔离 |
| --- | --- | --- |
| J1 | tests/shared_fixtures.py 的 analysis_dsl_rows(j1) 四客户与订单；tests/test_analysis_dsl_fixtures.py 的 SQL 得总计 1000、east 600、south 400、west 空 | 新 DSL 从原始事实重验；旧 J1 Artifact 不作 expected |
| J2 | 七/八月订单和九月零贡献；独立 SQL/Fraction 得 A/B/C/D 差值 -40/+20/-50/0、严格下降 {A,C}、九月均值 15 | 不从旧 compare/筛选结果反填预期 |
| J3 | 订单/明细两根和实际完整客户×渠道坐标；独立 SQL/Fraction 得 web 160/3、mobile 0、总体 40、当前行均值 130/3；权重反例 200/101 与 50.5 | 不平均子组比率或使用旧 ratio 输出 |
| J4 | 四对收入/订单数，手算平均秩的 Fraction 得 Spearman -2/5，并列秩 7/9；配对键/Null/常量等负例分列 | 不用候选 Spearman reducer 生成预期，旧系数不作为可上卷状态 |
| 安装包四题 | devtools/analysis_dsl_s4_p3/oracle.py 从原始 DuckDB 事实执行 SQL/平均秩；controller.py 分存 oracle、用户脚本、断源恢复、业务提示 | user/recovery JSON 是旧执行结果，只核对历史事实，不当新资格预期 |
| 故障注入 | test_analysis_dsl_j1_runtime.py 的来源失败、提交前停止、未知发布结果；test_analysis_dsl_s2_p1.py 多前驱提交失败；s2_p2/s2_p3/s3_p2 的部件/receipt 损坏；s3_p3 的来源失败禁止 Python 重试 | 可复用断点与「无成功 Artifact、旧 Artifact 可恢复」断言，不复用旧协议身份 |

独立 fixture 的历史复核命令为 make test TESTS='tests/test_analysis_dsl_fixtures.py'；旧 Runtime 的定向入口为 make runtime-test TESTS='tests/test_analysis_dsl_j1_runtime.py tests/test_analysis_dsl_s2_p1.py tests/test_analysis_dsl_s2_p2.py tests/test_analysis_dsl_s2_p3.py tests/test_analysis_dsl_s3_p2.py tests/test_analysis_dsl_s3_p3.py'。本轮未执行；它们不构成 R1/R9 新矩阵验收。

## 6. 未取得的证据与交接

- S4 P3 完整成功/失败 Agent 轨迹只在原 checkout 的忽略目录；manifest 固定本机原件 hash。跨 checkout 原件不可得，标「历史记录，待受控归档或重跑」。失败尝试原样保留，不能编辑为通过。
- S4 P3/P4 wheel 只在 /tmp；本轮只核对 hash，未重新安装或执行。C10 机器回执已跟踪，二进制未纳入本包。
- C0–C9 缺机器回执或原始 stdout 的格子按正文复跑；C3b Trino、C8 审查未重跑的远端、C9 保留拒绝与资源失败保持原状态。
- 本索引只承担 R0.1。C01–C18 去向、必需 owning spec、SQL 台账、六后端新矩阵与 R0 总验收仍属 R0.2–R0.6。
