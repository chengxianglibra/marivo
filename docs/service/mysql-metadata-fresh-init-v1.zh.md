# MySQL Metadata Fresh-init v1 契约冻结

状态：v1 frozen design；tasks 1.1 - 4.6 completed。

本文冻结 Marivo metadata store 从 SQLite-only 扩展到 MySQL 时的首版产品边界、支持矩阵、运行环境要求、生产可用标准与 fail-closed 策略。

本文是服务/运行时设计说明，不是 source/engine/mapping 的 HTTP API 契约。当前实现支持 `sqlite|mysql` metadata backend；SQLite 仍是本地默认，MySQL 只覆盖 fresh-init v1 的生产级共享 metadata store。

## v1 冻结结论

- MySQL metadata v1 只支持 fresh-init：空库初始化与新实例启动。
- 本轮不做 SQLite -> MySQL 迁移、双写、在线 backfill、自动 ALTER、自动 DROP 或局部补丁。
- metadata backend 支持矩阵只声明 `sqlite` 与 `mysql`；不为 PostgreSQL 或其他 backend 预留当前 contract。
- `sqlite` 仍是本地开发、本地 bootstrap 与多数单元测试默认 backend。
- `mysql` 是生产级共享 metadata store，用于 Marivo control-plane schema，不是 source adapter，也不是 execution engine。
- MySQL 初始化必须 fail closed：未知非空 schema、版本不满足、权限不足或关键表不完整时直接失败。

## 1. Fresh-init v1 产品边界

v1 的目标是让全新部署的 Marivo 可以把 metadata control-plane 表初始化到 MySQL，并以 MySQL 作为共享 metadata store 运行。

v1 明确支持：

- MySQL 空库初始化完整 metadata schema。
- 新实例基于 `metadata.engine=mysql` 和 MySQL 连接配置启动。
- 保留 SQLite 作为本地默认与测试默认。
- 让上层 session、step、artifact、finding、proposition、source、engine、mapping、semantic metadata 等 control-plane 写读面在 MySQL 后端保持同一外部行为。

v1 明确不支持：

- SQLite metadata 文件迁移到 MySQL。
- SQLite 与 MySQL 双写或读写切换。
- 在线 backfill、在线 schema migration、自动数据修复。
- 对已有未知 MySQL schema 做自动 ALTER、DROP 或局部补丁。
- 新增 MySQL source adapter 或 MySQL execution engine。

## 2. Metadata Backend 支持矩阵

| backend | v1 定位 | 默认使用场景 | 当前实现状态 |
| --- | --- | --- | --- |
| `sqlite` | 本地与测试默认 metadata backend | `init-local`、`serve-local`、单元测试、轻量本地开发 | 已实现 |
| `mysql` | 生产级共享 metadata backend | 多进程/多实例共享 control-plane metadata 的生产部署 | 已实现 fresh-init/runtime 接入 |

实现约束：

- `MetadataConfig`、`app_factory`、配置示例、错误信息和文档只应围绕 `sqlite|mysql` 收敛。
- 不在当前字段、枚举、文档示例或错误信息中预留 PostgreSQL 等未实现 backend。
- metadata backend 选择只影响 Marivo control-plane 存储，不改变 source、engine、mapping 的 HTTP API 管理边界。

## 3. MySQL 运行环境要求

MySQL metadata v1 的最小运行环境要求如下：

- MySQL 8.0.16+。
- 使用 InnoDB。
- 数据库字符集和排序规则使用 `utf8mb4` 族。
- 外键约束可用且真实生效。
- 应用账号具备当前 schema 下的 create table、create index、select、insert、update、delete 权限。
- 连接参数必须支持连接超时配置，错误输出不得泄露密码、token 或完整 DSN。

启动或初始化阶段必须显式检查这些前置条件。低版本 MySQL、权限不足、外键不可用或字符集不满足时，必须返回明确失败，不允许静默降级为 SQLite，也不允许继续以部分 schema 运行。

## 4. 生产可用标准

MySQL metadata v1 的实现必须满足以下生产可用标准：

- 连接管理：使用连接池或等价机制，避免每次 metadata 操作无界创建连接。
- 超时控制：连接建立、查询执行和健康检查路径有明确超时。
- 事务语义：多语句写路径必须支持 rollback，异常后不得留下半提交数据。
- 行输出：`query_rows` / `query_one` 必须保持 dict row contract，与 SQLite 后端对上层一致。
- 幂等写入：ignore insert、upsert、唯一约束冲突处理在 MySQL 与 SQLite 对外语义一致。
- 敏感信息：日志、异常、API 错误和测试断言不得泄露密码、token 或完整 DSN。
- 集成测试：未配置 MySQL DSN 时 skip；配置后覆盖初始化、DML、事务 rollback 与 app startup。
- 文档：operator 能按文档完成建库、授权、配置、启动、健康检查和常见错误排查。

每一项生产标准都必须在后续实现任务中对应到代码、测试或文档，不允许只停留在说明文字。

## 5. Fail-closed Bootstrap 策略

MySQL metadata v1 只初始化空库。初始化逻辑必须遵循以下策略：

1. 连接 MySQL 后先检查版本、引擎、字符集、权限和 schema 状态。
2. 如果目标 schema 为空，创建完整 Marivo metadata schema。
3. 如果目标 schema 非空且不是完整、已识别的当前 Marivo metadata schema，启动失败。
4. 如果关键表、索引、外键或 schema marker 不完整，启动失败。
5. 不对未知或不完整 schema 自动执行 ALTER、DROP、补丁修复或迁移。

该策略的目的不是支持历史数据库升级，而是避免在生产共享 MySQL 上误改非 Marivo schema 或半成品 schema。未来若需要 SQLite -> MySQL 迁移或 MySQL schema migration framework，应另起独立计划，包含导出、导入、校验、停机窗口、回滚与审计。

## 与现有服务契约的关系

- [`agent-runtime-target-resolution-bootstrap-config.zh.md`](./agent-runtime-target-resolution-bootstrap-config.zh.md) 冻结的是本地 bootstrap 默认配置；本地默认继续使用 SQLite。
- [`agent-runtime-target-resolution.md`](./agent-runtime-target-resolution.md) 描述 agent 本地运行时自动托管流程；MySQL metadata 不改变该流程的默认体验。
- [`source-execution-mapping-contract.md`](./source-execution-mapping-contract.md) 冻结 source、execution engine 与 mapping 的数据平面边界；metadata MySQL 只替换 control-plane store，不新增 source/engine/mapping 类型。

## 后续实现入口

任务 1.1 - 1.5 完成后，后续实现应从 metadata 方言层、MySQL DDL、store 构造、事务高频路径、集成测试和 operator 文档依次推进。实现期间不得把 MySQL support 简化为 driver swap；当前 SQLite SQL 中的 `INSERT OR IGNORE`、`ON CONFLICT`、`datetime('now')`、`PRAGMA`、`sqlite_master`、`pragma_table_info` 等都需要由后续方言层或 backend 专用实现承接。

## Metadata 方言层收敛状态（tasks 2.1 - 2.6）

任务 2.1 - 2.6 已落地为 metadata dialect contract 与 shared SQL 边界：

- Repository/runtime 继续使用 `?` 作为 canonical placeholder；backend store 在执行边界按 dialect 编译。
- SQLite dialect 保持 `INSERT OR IGNORE`、`ON CONFLICT` 与 `datetime('now')` 的既有行为；MySQL dialect 生成 `INSERT IGNORE`、`ON DUPLICATE KEY UPDATE` 与 `CURRENT_TIMESTAMP`。
- `MetadataStore` 提供 `insert_ignore`、`upsert_by_key` 与连接级 `execute_sql` helper，事务内写入也必须经过 dialect 编译。
- Shared runtime/repository 不再直接写 SQLite 专用 idempotent insert、upsert 或当前时间表达式。
- SQLite introspection 与 DDL 中的 `PRAGMA`、`sqlite_master`、`pragma_table_info`、SQLite timestamp default 只允许保留在 SQLite store、schema DDL、SQLite schema tests 或模板校验路径。

## DDL 与 Schema Bootstrap 状态（tasks 3.1 - 3.6）

任务 3.1 - 3.6 已落地为 DDL/bootstrap contract：

- `metadata_ddl_for_backend("sqlite"|"mysql")` 提供方言感知 DDL 入口；SQLite 保持现有 fresh-init 行为，MySQL 输出独立 DDL contract。
- MySQL DDL 使用 InnoDB 与 `utf8mb4`，主键、外键、唯一索引和普通索引列使用可索引长度类型，不依赖 prefix index 兜底。
- MySQL 时间字段使用 `CURRENT_TIMESTAMP` contract，不包含 SQLite-only `datetime('now')`、`AUTOINCREMENT`、partial index 或 SQLite trigger body。
- MySQL 8.0.16+ 可依赖 `CHECK`；低版本拒绝进入运行态由后续 MySQL store startup/preflight 接入执行。
- Bootstrap helper 以 fail-closed 方式区分空 schema、当前 schema 与未知/不完整 schema；不会自动 ALTER、DROP、迁移或修补未知表。
- Schema marker contract 记录 `backend`、`schema_version`、`created_at` 与 `ddl_fingerprint`，用于识别当前 fresh-init schema。

## MySQLMetadataStore 与配置接入状态（tasks 4.1 - 4.6）

任务 4.1 - 4.6 已落地为真实 MySQL metadata backend 运行接入：

- `MySQLMetadataStore` 使用 PyMySQL 作为可选依赖，保持 `initialize/connect/execute/execute_many/query_rows/query_one` 与 SQLite store 对上层一致；repository SQL 仍使用 canonical `?` placeholder，由 MySQL dialect 编译为 `%s`。
- `initialize()` 在 MySQL 连接上执行版本、字符集与 schema state preflight：空 schema 执行 MySQL DDL 并写入 schema marker；当前 schema 直接通过；未知表、缺 marker、缺关键表或 marker/fingerprint 不匹配时失败。
- `connect()` 通过 bounded connection pool 复用连接；store helper 正常路径提交，异常路径自动 rollback 并归还连接，保证多语句事务失败时不留下半提交数据。
- `MetadataConfig` 支持 `metadata.engine: sqlite|mysql`；SQLite 继续要求 `path`，MySQL 支持 DSN 或显式 `host/port/database/user/password/connect_timeout/pool_size/ssl` 字段，并在配置加载阶段拒绝非法组合。
- `app_factory` 在未显式注入 `metadata_store` 时按 `metadata.engine` 构造 SQLite/MySQL store；`db_path` programmatic carveout 继续保持 SQLite `.meta.sqlite` 行为。
- 错误输出与测试覆盖新增敏感信息脱敏，避免 DSN、password、SSL secret、环境变量敏感字段进入日志或 API error detail。

## Repository SQL 改造状态（tasks 5.1 - 5.6）

任务 5.1 - 5.6 已落地为 shared repository/runtime 的 SQL 方言边界：

- Session close/update、step metadata upsert、finding/proposition 幂等写入与 artifact commit transaction 均通过 `MetadataStore` dialect helper 执行，不在 shared runtime/repository 中直接写 SQLite 专用 DML。
- Artifact staged -> findings -> committed 仍保持单事务边界；事务内写入使用连接级 `execute_sql`，由 backend store 负责 placeholder 编译、commit 与异常 rollback。
- Intent/semantic 测试 seed helper 已改为 `insert_ignore`，用于 MySQL 集成测试的 shared seed 路径不依赖 SQLite `INSERT OR IGNORE`。
- Source/engine read serializer 对存量坏 JSON 行保持 degraded-read 行为：读接口不扩大为 500，而是返回 `not_ready` 与稳定 failure code。
- 静态扫描测试继续阻止 shared app code 和 intent seed 路径重新引入 SQLite-only idempotent insert、upsert 或当前时间表达式。
