# C10：最终 wheel 整体验收

日期：2026-09-23。状态：**C10 的有界安装包验收完成**；下述未运行及仍拒绝的单元不因此变成已支持。执行基线为 `d3aa5b94e4e387e0cb0f225b0a4801252d0f984b`（`lazy-dataset`，开始时工作区干净）；验收记录编写时，实施/验收改动尚未提交。依据 [C10 实施计划](2026-09-23-multisource-capability-c10-implementation-plan.md)、[总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)及[机器回执](2026-09-23-multisource-capability-c10-execution-receipts.json)。本次未改生产代码、公共 API、packaged skills 或包依赖。

## 1. 最终产物与安装来源

`make pypi-build pypi-check` 退出 0，生成 `marivo-0.5.3.dev0-py3-none-any.whl`，SHA-256 为 `5e52c10c3625b82f2e6853bc58f1fa4b67200d1ca7a8f4829b53fc0673d41b83`。六个安装包 profile（SQLite、PostgreSQL、MySQL、单节点 ClickHouse、双分片 ClickHouse、Trino）各自在仓库外新建 venv，`direct_url.json` 的 archive hash 均等于该值；安装探针在测试开始与结束核对所有已加载 `marivo` 模块都来自该 venv 的 site-packages。双分片 profile 只运行集群测试，其余五个 profile 均完成公共 Session 旅程。

公共旅程在真实受限 reader 下完成聚合、mean/weighted mean/ratio、关系、原生日期 Forecast 和现已开放的 exact distinct。管理员仅为 UUID 表准备/销毁测试行。插入拒绝经 reader 实测；重复关系身份导致失败且无新 Artifact；移除源表后，独立 PID 恢复 Session、精确命中并用 retained components 本地 rollup，新的源端计算失败且无新 Artifact。每轮数值、PID、SQL 角色及行/字节统计在机器回执中逐项保存；不把本地 rollup 的 primary 计数记作远程 SQL。

## 2. 已开放单元的 wheel 样本

安装包内复制测试源码到隔离目录，再运行各阶段已持有手算预期值的定向测试；它们导入的是已安装 wheel，不是 checkout 的 `marivo`。专项矩阵仍由原阶段验收持有，以下为 C10 样本而非逐单元重新认证。

| 能力 | 本次 wheel 结果 | 证据边界 |
| --- | --- | --- |
| C1–C2 | 五后端公共源 schema/负向与 Boolean、字符串、timestamp 传输和冷读样本通过 | 更多 UInt 极值、无关复杂列等单元引用 [C1](2026-09-16-multisource-capability-c1-acceptance.md)、[C2](2026-09-16-multisource-capability-c2-acceptance.md) |
| C3a/C3b | 五后端 native timestamp 桶、SQLite/MySQL/PostgreSQL/ClickHouse 解析轴样本通过 | Trino C3b 仍未验收；多单位桶和完整 DST 负向引用 [C3a](2026-09-18-multisource-capability-c3a-acceptance.md)、[C3b](2026-09-19-multisource-capability-c3b-acceptance.md) |
| C4 | PostgreSQL/MySQL/ClickHouse 计算型 Decimal 与 Linear、Trino 计算型 Decimal 样本通过；MySQL Decimal mean 与 Trino Decimal mean 的拒绝样本通过 | SQLite 的计算型专项及其余精度/溢出单元引用 [C4](2026-09-20-multisource-capability-c4-acceptance.md) |
| C5 | 五后端视图；Trino `noniceberg` memory catalog 的真实读、metadata 审计和内表拒绝；ClickHouse 真实两分片聚合、跨片重复身份拒绝和无 `FINAL`/去重 SQL 审计通过 | 双分片 reader 拒写与其它表形态的完整证据见 [C5](2026-09-21-multisource-capability-c5-acceptance.md) |
| C6 | 五后端 fiscal-month 日历、月重置累计、已准入 status-time fold 样本通过 | validity、其它窗口及 MySQL first/last 拒绝沿用 [C6](2026-09-21-multisource-capability-c6-acceptance.md) |
| C7 | 五后端 exact distinct/private state 与精确分布样本通过；SQLite 重复 membership pair 拒绝 | 更多私有状态/冷续算形状见 [C7](2026-09-22-multisource-capability-c7-acceptance.md) |
| C8 | 五后端 Entity correlation；SQLite/PostgreSQL/Trino/ClickHouse 已开放 hidden-axis attribution 样本通过；Trino Top-K 预查询拒绝通过 | MySQL hidden-axis attribution、Candidate/Driver 仍关闭；细分矩阵见 [C8](2026-09-22-multisource-capability-c8-acceptance.md) |
| C9 | PostgreSQL Event、Lifecycle 和直接 reducer；Trino Event/Lifecycle；ClickHouse Event/Lifecycle 在 wheel 上通过 | SQLite/MySQL 无正向开放单元；其它直接派生方法仍依 [C9](2026-09-22-multisource-capability-c9-acceptance.md) 保持关闭/待验收 |

[C0 基线](2026-09-16-multisource-capability-c0-acceptance.md)仅作目标/历史证据起点，不被本轮 wheel 样本改写。C8 ClickHouse Top-K 曾在组合 wheel 运行及紧接的单独重跑中遇到服务端 code 241、2 GiB 内存上限；在服务组切换并恢复单节点后，同一最终 wheel 的独立用例 **1 passed**。本次记录两个结果，不把有条件的成功写成组合负载下一定通过，也不把资源失败误判为能力准入拒绝。

## 3. 提交边界、命令与环境

SQLite 的 `sqlite3` trace 与 MySQL 的 `SSCursor.execute` 独立捕获受限 reader SQL；公共源旅程的 validation、primary（SQLite 还包括 storage check）按原文/次数匹配 Runtime 已成功提交回执，机器文件只保存角色、状态及 SQL SHA-256，不保存凭据或私有行值。PostgreSQL Lifecycle 用原生 `Cursor`/`ServerCursor` 拦截，Trino Lifecycle 用 `TrinoRequest.post` 及同一只读事务 ID 对账，ClickHouse Lifecycle 用 `system.query_log` 与 `lifecycle_bundle` 全文对账。集群样本只审 Runtime 提交与无去重语句，未增加独立集群 server-log 捕获；驱动内部未被这些钩子覆盖的操作不据此宣称已完整观察。

最终门禁：定向两个测试文件的 Ruff/mypy（mypy `--follow-imports=silent`，避免既有 service helper 的缺失类型桩混入结论）通过；末轮 `make check-agent` 退出 0，**5687 passed / 16 skipped**，类型、lint、import contracts 与 API 文档均通过；`git diff --check` 通过。构建/check 退出 0。安装包四组最终命令均退出 0：`MARIVO_INSTALLED_BACKENDS=sqlite,mysql` 为 **2 passed / 4 selection skips**，`postgres,clickhouse` 为 **2 / 4**，`clickhouse-cluster` 为 **1 / 5**，`trino` 为 **1 / 5**；均设置 `MARIVO_INSTALLED_MULTISOURCE_TEST=1`、`MARIVO_MULTISOURCE_EVIDENCE_DIR=/tmp/marivo-capability-c10`，后三组及 MySQL 所在组使用按既有 runbook 重建的本机 `mysqlclient` 测试 wheel 的 `PIP_FIND_LINKS`。139 个定向安装包专项用例全部通过；skip 只是未选择的 profile，不算证据。实际子命令、退出码、JUnit case 名和统计见机器回执。

初轮失败未计为绿证据：旧探针对 C7 distinct 的拒绝预期与 C3a 之后的 metadata/validation 分类过期；MySQL 缓存驱动 wheel 缺 `_mysql_affected_rows` 符号；ClickHouse Top-K 的上述资源失败。修复的是测试断言/本机驱动来源；没有放宽生产准入或服务资源限制。Trino 启动后重建易失 memory catalog，再运行 Iceberg/非 Iceberg 旅程。结束时服务状态恢复为起始轮廓：PostgreSQL analysis、MySQL analysis、单节点 ClickHouse healthy，Trino/双分片/Trino catalog PostgreSQL 停止。未启动 MinIO、未运行 `release-check`；验收运行当时未提交、推送或发布。

## 4. 剩余差距

本次 wheel 样本未逐格重跑 C1–C9 专项矩阵。Trino C3b、MySQL C4 Decimal mean/div、Trino C4 Decimal、MySQL C6 first/last fold、MySQL C8 attribution、所有远程 Candidate/Driver，以及 C9 验收表中的 SQLite/MySQL Event/Lifecycle 与 Trino/ClickHouse 未开放的直接派生形态，继续按各 owner 记录保持未验收或拒绝。C9 SQLite/MySQL 的预 I/O 拒绝未在本次 wheel 单独重跑，属于引用专项证据，不算 C10 wheel 通过单元。ClickHouse Top-K 的组合负载资源敏感性仍是安装包运行限制。T3 正向复杂值、任意跨源联邦和远程 retained 上传不在 C10 开放范围。
