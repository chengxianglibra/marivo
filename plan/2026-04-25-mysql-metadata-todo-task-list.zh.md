# MySQL Metadata Fresh-init v1 Todo Task List

状态：执行中。本文为 `plan/` 下的本地计划文件，`plan/` 在当前仓库中被 gitignore。本文按 2026-04-25 的 MySQL metadata Fresh-init v1 计划重建，并同步任务 1.1 - 1.5 的完成状态。

## 概览

目标是在 Marivo 当前 SQLite-only metadata layer 之外，新增生产级 MySQL metadata backend。首版范围固定为 Fresh-init v1：只支持空库初始化和新实例启动，不支持 SQLite -> MySQL 迁移、双写、在线 backfill 或自动 schema migration。

当前实现事实：

- `MetadataStore` 只是薄抽象，当前主要实现是 `SQLiteMetadataStore`。
- `MetadataConfig.engine` 当前只接受 `sqlite`。
- `app_factory` 在未注入 `metadata_store` 时构造 `SQLiteMetadataStore`。
- 现有 SQL 使用 SQLite 专用语法与 introspection，例如 `INSERT OR IGNORE`、`ON CONFLICT`、`datetime('now')`、`PRAGMA`、`sqlite_master`、`pragma_table_info`。
- `tests/shared_fixtures.py` 与 `tests/conftest.py` 依赖 SQLite metadata template 缓存，schema 变更必须同步刷新模板版本和 shape 校验。

## 交付原则

- 先冻结 contract，再做 runtime/schema 实现。
- MySQL metadata 是 control-plane store，不是 source adapter，也不是 execution engine。
- SQLite 继续作为本地和测试默认 backend。
- MySQL 按生产可用标准实现，但 v1 只支持 fresh-init。
- 所有迁移、双写、backfill、在线 schema migration 都作为 non-goals。

## 一、Scope 与 Contract 冻结

- [x] 任务 1.1：冻结 Fresh-init v1 产品边界
  - 交付物：[`docs/service/mysql-metadata-fresh-init-v1.zh.md`](../docs/service/mysql-metadata-fresh-init-v1.zh.md)
  - 关键内容：MySQL metadata 首版只支持空库初始化与新实例启动；不做 SQLite -> MySQL 迁移
  - 验收标准：实现文档、配置文档、测试说明中均不承诺迁移、双写、在线 backfill

- [x] 任务 1.2：冻结 metadata backend 支持矩阵
  - 交付物：[`docs/service/mysql-metadata-fresh-init-v1.zh.md`](../docs/service/mysql-metadata-fresh-init-v1.zh.md)
  - 范围：`sqlite` 为本地/测试默认，`mysql` 为生产级共享 metadata store
  - 验收标准：`MetadataConfig`、`app_factory`、文档和错误信息只声明 `sqlite|mysql`，不预留 PostgreSQL 文案

- [x] 任务 1.3：冻结 MySQL 运行环境要求
  - 交付物：[`docs/service/mysql-metadata-fresh-init-v1.zh.md`](../docs/service/mysql-metadata-fresh-init-v1.zh.md)
  - 最小要求：MySQL 8.0.16+、InnoDB、utf8mb4、外键约束可用、应用账号具备 create table/index 与 DML 权限
  - 验收标准：低版本 MySQL 或不满足权限时启动前/初始化阶段给出明确失败，不静默降级

- [x] 任务 1.4：冻结生产可用标准
  - 交付物：[`docs/service/mysql-metadata-fresh-init-v1.zh.md`](../docs/service/mysql-metadata-fresh-init-v1.zh.md)
  - 范围：连接池、连接超时、事务 rollback、dict row 输出、敏感信息脱敏、集成测试、文档
  - 验收标准：每项标准都有对应实现任务和测试/检查项

- [x] 任务 1.5：冻结 fail-closed 策略
  - 交付物：[`docs/service/mysql-metadata-fresh-init-v1.zh.md`](../docs/service/mysql-metadata-fresh-init-v1.zh.md)
  - 关键内容：MySQL 首版只初始化空库；发现未知非空 schema、版本不满足或关键表不完整时失败
  - 验收标准：不会对已有 MySQL schema 做自动 ALTER、DROP、迁移或局部补丁

### 冻结附录（任务 1.1 - 1.5）

后续 2.x - 7.x 的 schema、storage、runtime、tests 和 docs 改动都以 [`docs/service/mysql-metadata-fresh-init-v1.zh.md`](../docs/service/mysql-metadata-fresh-init-v1.zh.md) 为准。

冻结结论：

- v1 只支持 MySQL metadata fresh-init。
- SQLite 保留为本地开发和测试默认 backend。
- MySQL 是生产级共享 metadata store，属于 Marivo control-plane schema。
- v1 不支持 SQLite -> MySQL 迁移、双写、在线 backfill、自动 ALTER、自动 DROP 或局部补丁。
- MySQL 初始化必须 fail closed，避免误改未知生产 schema。

## 二、Metadata 方言层收敛

- [ ] 任务 2.1：新增 metadata dialect 抽象
  - 交付物：`MetadataDialect` 或等价 helper
  - 最小能力：placeholder、now expression、ignore insert、upsert、DDL 选择、backend name
  - 验收标准：上层 repository 不需要知道 MySQL driver 占位符和 upsert 语法

- [ ] 任务 2.2：收敛共享 repository SQL
  - 交付物：替换共享路径里的 SQLite 专用 SQL
  - 范围：session、step、artifact、finding、proposition、source/engine/mapping、step metadata 等高频写读路径
  - 验收标准：共享 repository/runtime 不再新增 SQLite-only SQL

- [ ] 任务 2.3：保留 SQLite backend 行为
  - 交付物：SQLite dialect / SQLite store 适配
  - 验收标准：现有 SQLite 单元测试不回归

## 三、MySQL Schema 与 Bootstrap

- [ ] 任务 3.1：新增 MySQL DDL contract
  - 交付物：MySQL metadata schema 定义
  - 范围：表、主键、唯一索引、外键、时间字段、JSON 字段、schema marker
  - 验收标准：MySQL 空库可一次性创建完整 schema

- [ ] 任务 3.2：实现空库检测与 schema marker
  - 交付物：MySQL initialize 前置检查
  - 验收标准：空库初始化；未知非空 schema fail closed

- [ ] 任务 3.3：实现 MySQL 环境校验
  - 交付物：版本、InnoDB、utf8mb4、外键、权限检查
  - 验收标准：不满足前置条件时返回明确错误且不降级

## 四、配置与 Store 构造

- [ ] 任务 4.1：扩展 `MetadataConfig`
  - 交付物：`metadata.engine` 支持 `sqlite|mysql`
  - 验收标准：配置校验只接受当前支持矩阵

- [ ] 任务 4.2：新增 MySQL 连接配置
  - 交付物：最小 DSN/host/port/database/user/password 配置模型
  - 验收标准：敏感字段不进入日志和错误详情

- [ ] 任务 4.3：更新 `app_factory` store 构造
  - 交付物：按 metadata backend 构造 SQLite 或 MySQL store
  - 验收标准：SQLite 现有路径不变，MySQL 配置路径可启动到 initialize

## 五、运行时高频写读路径

- [ ] 任务 5.1：覆盖 session lifecycle
  - 交付物：MySQL 后端 session create/read/update/close
  - 验收标准：对外 contract 与 SQLite 一致

- [ ] 任务 5.2：覆盖 artifact / step commit
  - 交付物：事务化写入路径
  - 验收标准：异常时 rollback，不残留半提交数据

- [ ] 任务 5.3：覆盖 finding / proposition 幂等写入
  - 交付物：MySQL upsert / ignore insert 行为
  - 验收标准：唯一约束冲突处理与 SQLite 对外语义一致

- [ ] 任务 5.4：覆盖 source / engine / mapping registry
  - 交付物：注册、读取、ready/not_ready 降级读行为
  - 验收标准：malformed stored row 仍按现有 contract 降级，不变成 500

## 六、测试与验证

- [ ] 任务 6.1：刷新 SQLite metadata template
  - 交付物：template version bump 与 shape validator
  - 验收标准：旧缓存不会掩盖 schema drift

- [ ] 任务 6.2：新增 MySQL 集成测试开关
  - 交付物：pytest marker / env loader
  - 验收标准：未配置 MySQL DSN 时 skip；配置后运行 MySQL suite

- [ ] 任务 6.3：覆盖 MySQL initialize 与 schema shape
  - 交付物：MySQL storage tests
  - 验收标准：空库完整初始化，未知非空库拒绝启动

- [ ] 任务 6.4：覆盖 MySQL DML 与事务 rollback
  - 交付物：repository tests 与失败注入测试
  - 验收标准：MySQL 行为与 SQLite 对外 contract 一致

- [ ] 任务 6.5：新增 SQLite 专用 SQL 静态扫描
  - 交付物：测试或 lint helper
  - 验收标准：共享 repository/runtime 不再新增 SQLite 专用 SQL

## 七、文档与运维说明

- [ ] 任务 7.1：更新 agent guide
  - 交付物：[`docs/agent-guide.md`](../docs/agent-guide.md)
  - 关键内容：metadata 后端选择、SQLite 本地默认、MySQL 生产路径、禁止把 metadata backend 与 source/engine 混淆
  - 验收标准：agent 不会把 MySQL metadata 误当 execution engine 或 source adapter

- [ ] 任务 7.2：更新配置示例
  - 交付物：`marivo.example.yaml` 或相关 quickstart
  - 验收标准：示例可被配置模型解析，且不包含真实密码

- [ ] 任务 7.3：更新 API/运行文档
  - 交付物：metadata runtime docs
  - 验收标准：operator 能按文档创建空库、配置 Marivo、启动并验证 `/health`

- [ ] 任务 7.4：补充 MySQL 权限与备份建议
  - 交付物：operator runbook
  - 验收标准：生产部署前置条件清晰，不依赖开发者阅读源码

- [ ] 任务 7.5：记录 non-goals 与后续扩展入口
  - 交付物：decision record
  - 验收标准：后续需求不会被误认为已在 v1 中承诺

## 验收矩阵

- [ ] `make test TESTS='tests/test_storage.py tests/test_config.py'` 通过。
- [ ] MySQL 集成测试在未配置 MySQL 时稳定 skip，在配置 MySQL DSN 时通过。
- [ ] `make typecheck` 通过。
- [ ] `make lint` 通过。
- [ ] 静态扫描能阻止共享 repository/runtime 中新增 SQLite 专用 SQL。
- [ ] MySQL 空库启动后，`metadata_store.initialize()` 能创建完整 schema。
- [ ] MySQL 后端可完成 session create、step/artifact commit、finding/proposition idempotent insert、step metadata upsert。
- [ ] 异常注入能证明 MySQL 多语句事务 rollback 生效。
- [ ] 文档说明 Fresh-init v1，不承诺 SQLite -> MySQL 迁移。

## 默认假设

- 本文只规划 MySQL metadata，不规划 MySQL source adapter 或 MySQL execution engine。
- MySQL 首版按生产可用标准实现，但只支持 fresh-init 空库。
- SQLite 保留为本地开发和大多数单元测试默认 backend。
- metadata schema 仍是 Marivo control-plane schema，不改变 typed intent、source/engine/mapping 的外部业务 contract。
- 若未来需要 SQLite -> MySQL 迁移，应另起独立计划，包含导出、导入、校验、停机窗口、回滚与审计。
