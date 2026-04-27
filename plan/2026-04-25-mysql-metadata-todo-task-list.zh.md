# MySQL Metadata 改造实施 Todo Task List

## 概述

本文将当前 SQLite metadata 实现调研结果拆解为一份可直接落地开发的实施清单，目标是在 **保持 HTTP-only 边界、保留 SQLite 本地/测试默认、引入 MySQL 作为生产级共享 metadata store** 的前提下，完成 MySQL metadata fresh-init v1 改造。

一句话结论：

- v1 先做“**scope 冻结 + metadata 方言层 + MySQL DDL/bootstrap + MySQLMetadataStore/config 接入 + repository SQL 收敛 + 集成测试 + 文档运维说明**”。
- 不做 SQLite 到 MySQL 数据迁移，不做在线 schema migration，不做灰度双写，不做跨后端同步，不扩展成 PostgreSQL 泛化实现。
- 当前 `MetadataStore` 抽象较薄，上层 repository 大量直接写 SQL；MySQL 改造必须先收敛 SQLite 专用 SQL 与 DDL 差异，不能只新增一个连接类。

## 文档依据

- [`app/storage/metadata.py`](/Users/lichengxiang/source/oss/marivo/app/storage/metadata.py)
- [`app/storage/sqlite_metadata.py`](/Users/lichengxiang/source/oss/marivo/app/storage/sqlite_metadata.py)
- [`app/storage/schema.py`](/Users/lichengxiang/source/oss/marivo/app/storage/schema.py)
- [`app/config.py`](/Users/lichengxiang/source/oss/marivo/app/config.py)
- [`app/api/app_factory.py`](/Users/lichengxiang/source/oss/marivo/app/api/app_factory.py)
- [`tests/shared_fixtures.py`](/Users/lichengxiang/source/oss/marivo/tests/shared_fixtures.py)
- [`tests/test_storage.py`](/Users/lichengxiang/source/oss/marivo/tests/test_storage.py)
- [`tests/test_config.py`](/Users/lichengxiang/source/oss/marivo/tests/test_config.py)

## 当前实现对照

当前仓库基线的关键事实：

- `MetadataStore` 只定义 `initialize/connect/execute/execute_many/query_rows/query_one`，没有方言、事务、upsert、ignore insert 等能力分层。
- `SQLiteMetadataStore` 每次打开 file-backed SQLite 连接，设置 `PRAGMA journal_mode=WAL` 与 `PRAGMA foreign_keys=ON`，并直接迭代 `METADATA_DDL` 初始化 schema。
- `MetadataConfig.engine` 当前只允许 `sqlite`，`app_factory` 在未注入 `metadata_store` 时固定构造 `SQLiteMetadataStore`。
- `METADATA_DDL` 注释声称跨 SQLite/MySQL/PostgreSQL，但实际包含 `TEXT PRIMARY KEY`、`DEFAULT (datetime('now'))`、SQLite 风格 timestamp 表达式等 MySQL 风险点。
- runtime/repository/test 中存在 SQLite 专用写法：`INSERT OR IGNORE`、`ON CONFLICT ... DO UPDATE`、`datetime('now')`、`PRAGMA`、`sqlite_master`、`pragma_table_info`。
- 测试加速依赖 SQLite metadata 模板缓存；schema 变更必须同步更新 `_METADATA_TEMPLATE_VERSION` 与模板校验，否则旧模板可能掩盖 schema drift。

因此，MySQL 改造的重点不是“替换 driver”，而是把 shared metadata 访问路径从 SQLite 方言中解耦出来，并为 MySQL 建立可验证的 fresh-init 生产路径。

## 实施范围

### 本次必须覆盖

- 支持 `metadata.engine = sqlite | mysql`。
- 新增 MySQL metadata 空库初始化路径。
- 新增生产可用的 `MySQLMetadataStore`，包含连接池、超时、事务 rollback、dict row 输出。
- 收敛共享 repository 中的 SQLite 专用 SQL。
- 建立 MySQL DDL、外键、唯一约束、upsert、ignore insert、事务行为的集成测试。
- 保留 SQLite 作为本地开发和多数单元测试默认 metadata store。
- 更新文档、配置示例、agent guide 与运维说明。

### 本次明确不做

- SQLite metadata 到 MySQL 的数据迁移。
- 启动时自动改写已有 SQLite 或 MySQL schema。
- 在线 schema migration / backfill / reset 工具。
- SQLite/MySQL 双写、双读、灰度同步。
- PostgreSQL 或其他 metadata backend 泛化实现。
- 改变 Marivo HTTP-only 边界。
- 把 MySQL metadata 与 source/execution engine 的 MySQL/Trino 能力混为一体。

## 交付原则

- 先冻结后端 contract，再改 DDL/store，最后收敛 repository SQL 与测试。
- 方言差异必须集中表达，避免业务代码继续散落 `INSERT OR IGNORE`、`ON CONFLICT`、`datetime('now')` 等写法。
- MySQL 首版只面向 fresh-init 空库；对未知非空 schema 必须 fail closed。
- 事务语义必须以后端无关方式验收：成功时整体提交，异常时整体回滚。
- 配置、日志、错误信息不得泄露 MySQL 密码或敏感 DSN。
- 新增测试必须能在无 MySQL 环境时稳定 skip，不能拖慢默认单元测试路径。

## 建议实施顺序

1. T1 冻结 Fresh-init v1 scope、支持矩阵与生产可用标准
2. T2 抽出 metadata 方言层与 SQL helper
3. T3 拆分 SQLite/MySQL DDL 与 schema bootstrap
4. T4 实现 `MySQLMetadataStore` 与配置接入
5. T5 改造 repository/runtime 主路径 SQL
6. T6 补齐测试矩阵与静态防回归检查
7. T7 更新文档、示例与运维说明

说明：

- T2 是 T3/T5 的前置；没有方言层时，MySQL 支持会变成业务代码里的分支散落。
- T3 与 T4 可并行推进，但必须以同一份 MySQL DDL contract 为准。
- T5 优先覆盖 artifact commit、finding/proposition idempotent insert、step metadata upsert、session close/update 等运行时高频路径。
- T6 的 MySQL 集成测试不进入默认无条件依赖；通过环境变量启用，缺少配置时 skip。

## Todo Task List

## 一、Scope 与 Contract 冻结

- [x] 任务 1.1：冻结 Fresh-init v1 产品边界
  - 交付物：scope note / decision record
  - 关键内容：MySQL metadata 首版只支持空库初始化与新实例启动；不做 SQLite -> MySQL 迁移
  - 验收标准：实现文档、配置文档、测试说明中均不承诺迁移、双写、在线 backfill

- [x] 任务 1.2：冻结 metadata backend 支持矩阵
  - 交付物：support matrix
  - 范围：`sqlite` 为本地/测试默认，`mysql` 为生产级共享 metadata store
  - 验收标准：`MetadataConfig`、`app_factory`、文档和错误信息只声明 `sqlite|mysql`，不预留 PostgreSQL 文案

- [x] 任务 1.3：冻结 MySQL 运行环境要求
  - 交付物：operator requirement note
  - 最小要求：MySQL 8.0.16+、InnoDB、utf8mb4、外键约束可用、应用账号具备 create table/index 与 DML 权限
  - 验收标准：低版本 MySQL 或不满足权限时启动前/初始化阶段给出明确失败，不静默降级

- [x] 任务 1.4：冻结生产可用标准
  - 交付物：production readiness checklist
  - 范围：连接池、连接超时、事务 rollback、dict row 输出、敏感信息脱敏、集成测试、文档
  - 验收标准：每项标准都有对应实现任务和测试/检查项

- [x] 任务 1.5：冻结 fail-closed 策略
  - 交付物：bootstrap failure policy
  - 关键内容：MySQL 首版只初始化空库；发现未知非空 schema、版本不满足或关键表不完整时失败
  - 验收标准：不会对已有 MySQL schema 做自动 ALTER、DROP、迁移或局部补丁

## 二、Metadata 方言层收敛

- [x] 任务 2.1：新增 metadata dialect 抽象
  - 交付物：`MetadataDialect` 或等价 helper
  - 最小能力：placeholder、now expression、ignore insert、upsert、DDL 选择、backend name
  - 验收标准：上层 repository 不需要知道 MySQL driver 占位符和 upsert 语法

- [x] 任务 2.2：统一参数占位符处理
  - 交付物：SQL 编译/转换 helper
  - 关键内容：SQLite 保持 `?`，MySQL 使用 driver 需要的占位符；转换不得误改字符串字面量
  - 验收标准：相同 repository SQL 能在 SQLite/MySQL 两个 backend 下执行，参数顺序一致

- [x] 任务 2.3：统一当前时间表达式
  - 交付物：`dialect.now_sql()` 或应用层 timestamp helper
  - 范围：DDL default、session close/update、step metadata upsert、semantic helper 中的 update timestamp
  - 验收标准：shared runtime 代码不再直接写 `datetime('now')`

- [x] 任务 2.4：统一 idempotent insert 语义
  - 交付物：`insert_ignore` helper 或 repository 方法
  - 覆盖对象：findings、propositions、proposition seed refs、测试 seed helpers 中的 source/engine/mapping 插入
  - 验收标准：重复插入相同主键/唯一键在 SQLite/MySQL 下均为 no-op，不抛异常且不会覆盖既有行

- [x] 任务 2.5：统一 upsert 语义
  - 交付物：`upsert_by_key` helper 或专用 repository 方法
  - 覆盖对象：`step_metadata` 以及后续确认为 upsert 的共享路径
  - 验收标准：SQLite 的 `ON CONFLICT` 与 MySQL 的 `ON DUPLICATE KEY UPDATE` 行为一致，`updated_at` 正确刷新

- [x] 任务 2.6：建立 SQLite 专用 SQL 使用边界
  - 交付物：code guideline + static check
  - 范围：`PRAGMA`、`sqlite_master`、`pragma_table_info` 只允许出现在 SQLite store、SQLite schema tests、SQLite template validator 中
  - 验收标准：共享 repository/runtime 代码新增 SQLite 专用 SQL 时测试失败

## 三、DDL 与 Schema Bootstrap

- [x] 任务 3.1：拆分方言感知 metadata DDL
  - 交付物：SQLite DDL 与 MySQL DDL 生成/选择机制
  - 关键内容：保留 SQLite 现有 fresh-init 行为，同时为 MySQL 输出独立可执行 DDL
  - 验收标准：`SQLiteMetadataStore.initialize()` 与 `MySQLMetadataStore.initialize()` 不共享不可执行的裸 SQL 字符串

- [x] 任务 3.2：修正 MySQL 主键与索引列类型
  - 交付物：MySQL DDL contract
  - 范围：所有 `TEXT PRIMARY KEY`、唯一索引列、外键列改为可索引 `VARCHAR(n)` 或明确长度类型
  - 验收标准：MySQL InnoDB 可成功创建所有表、主键、唯一索引、外键，不依赖 prefix index 兜底

- [x] 任务 3.3：修正 MySQL timestamp default
  - 交付物：MySQL 时间字段 DDL
  - 范围：`created_at/updated_at/ended_at` 等字段默认值和更新写法
  - 验收标准：MySQL 空库初始化不出现 `datetime('now')` 语法错误，写入后时间字段非空且格式稳定

- [x] 任务 3.4：校验 MySQL check/constraint 策略
  - 交付物：constraint decision note
  - 关键内容：MySQL 8.0.16+ 可依赖 `CHECK`；跨版本风险由启动前版本检查兜住
  - 验收标准：低版本 MySQL 不进入运行态；支持版本中关键 `CHECK`、FK、unique 行为有集成测试覆盖

- [x] 任务 3.5：实现 MySQL 空库检测
  - 交付物：bootstrap preflight
  - 规则：目标 database 为空或只包含允许的 metadata bootstrap 标记时可初始化；存在未知业务表时失败
  - 验收标准：不会误把已有非 Marivo schema 当成可迁移目标，也不会自动删除未知表

- [x] 任务 3.6：建立 schema version/shape 记录
  - 交付物：metadata schema marker 表或等价记录
  - 最小字段：backend、schema_version、created_at、ddl_fingerprint
  - 验收标准：启动时能区分当前 fresh-init schema 与未知/过期 schema，并给出明确错误

## 四、MySQLMetadataStore 与配置接入

- [x] 任务 4.1：实现 `MySQLMetadataStore`
  - 交付物：metadata store 类
  - 最小接口：`initialize/connect/execute/execute_many/query_rows/query_one`
  - 验收标准：返回 row shape 与 SQLite 一致，`query_one` 空结果返回 `None`

- [x] 任务 4.2：实现连接池与连接参数
  - 交付物：connection factory / pool 配置
  - 最小参数：host、port、database、user、password、connect_timeout、pool_size、ssl
  - 验收标准：连接失败、认证失败、超时失败均有明确错误；密码不进入日志

- [x] 任务 4.3：实现事务 rollback 语义
  - 交付物：`connect()` context manager 行为收敛
  - 规则：正常路径由调用方或 store helper commit；异常路径自动 rollback 并关闭/归还连接
  - 验收标准：artifact staged insert 后模拟 finding 写入失败，MySQL 中不残留 staged artifact

- [x] 任务 4.4：扩展 metadata config model
  - 交付物：`MetadataConfig` 变更
  - 范围：`engine: sqlite|mysql`；SQLite 保持 `path`；MySQL 支持 DSN 或显式连接字段
  - 验收标准：非法组合在配置加载阶段失败，例如 `engine=mysql` 但缺少 database/user

- [x] 任务 4.5：改造 app storage resolution
  - 交付物：`app_factory` metadata store 构造逻辑
  - 规则：显式 `metadata_store` 优先；未注入时按 `metadata.engine` 构造 SQLite/MySQL；`db_path` programmatic carveout 保持 SQLite meta 文件行为
  - 验收标准：现有 SQLite 启动测试不回归，MySQL 配置可启动并初始化 metadata store

- [x] 任务 4.6：脱敏配置与错误输出
  - 交付物：logging/error redaction helper
  - 范围：DSN、password、SSL secret、环境变量中的敏感字段
  - 验收标准：失败日志和 API error detail 不包含明文密码

## 五、Repository SQL 改造

- [x] 任务 5.1：改造 session close/update SQL
  - 交付物：`SessionManager` 更新时间写法
  - 范围：`ended_at`、`updated_at` 等字段
  - 验收标准：SQLite/MySQL 下关闭 session 均能写入稳定时间，不再直接使用 `datetime('now')`

- [x] 任务 5.2：改造 step metadata upsert
  - 交付物：`StepMetadataRepository.upsert` 方言化
  - 范围：`ON CONFLICT(step_id) DO UPDATE` 替换为后端无关 helper
  - 验收标准：同一 `step_id` 重复 upsert 后只有一行，payload 与 `updated_at` 被更新

- [x] 任务 5.3：改造 finding/proposition idempotent insert
  - 交付物：evidence repositories 方言化
  - 范围：`FindingRepository`、`PropositionRepository`、seed refs 等 `INSERT OR IGNORE` 路径
  - 验收标准：重复 materialization 不产生重复行，不因唯一键冲突失败

- [x] 任务 5.4：改造 artifact commit 事务
  - 交付物：artifact staged -> findings -> committed 事务路径收敛
  - 关键内容：多语句事务不依赖 SQLite connection 细节；失败时 rollback
  - 验收标准：任一 finding 插入失败时 artifact 不进入 committed，也不残留半写入 findings

- [x] 任务 5.5：改造测试 seed helper 中的共享 SQL
  - 交付物：测试 helper 或 repository seed API
  - 范围：`tests/semantic_test_helpers.py`、intent test seed 中的 `INSERT OR IGNORE`
  - 验收标准：用于 MySQL 集成测试的 seed 逻辑不依赖 SQLite 语法

- [x] 任务 5.6：保留 degraded-read 行为
  - 交付物：read serializer 回归测试
  - 范围：旧行缺少可选 JSON、坏 JSON、not_ready degraded read
  - 验收标准：MySQL 接入不把既有 degraded-read 语义扩大为 500，除非当前代码已定义为硬失败

## 六、测试与验证

- [ ] 任务 6.1：保留 SQLite storage/config 回归测试
  - 交付物：现有测试更新
  - 命令：`make test TESTS='tests/test_storage.py tests/test_config.py'`
  - 验收标准：SQLite 初始化、模板校验、配置加载、app startup 现有行为不回归

- [ ] 任务 6.2：新增 MySQL 集成测试开关
  - 交付物：pytest marker / env loader
  - 规则：未配置 MySQL DSN 时 skip；配置存在时创建隔离测试 database/schema
  - 验收标准：默认 `make test` 不依赖本机 MySQL；CI 可按需启用 MySQL suite

- [ ] 任务 6.3：覆盖 MySQL initialize 与 schema shape
  - 交付物：MySQL storage tests
  - 范围：表、主键、外键、唯一索引、schema marker、unknown non-empty schema fail closed
  - 验收标准：MySQL 空库能完整初始化；非空未知库拒绝启动

- [ ] 任务 6.4：覆盖 MySQL DML 语义
  - 交付物：MySQL repository tests
  - 范围：query row shape、execute_many、ignore insert、upsert、FK cascade、unique constraint
  - 验收标准：MySQL 行为与 SQLite 对外 contract 一致

- [ ] 任务 6.5：覆盖事务 rollback
  - 交付物：失败注入测试
  - 范围：artifact commit、canonical downstream 关键写路径
  - 验收标准：异常后 MySQL 不残留半提交行；SQLite 现有事务行为不回归

- [ ] 任务 6.6：覆盖 MySQL app startup
  - 交付物：配置驱动 app factory 测试
  - 范围：`metadata.engine=mysql` 配置解析、store 构造、initialize、基础 session API
  - 验收标准：使用 MySQL 空库启动后可完成 session create 与基础读面

- [ ] 任务 6.7：新增 SQLite 专用 SQL 静态扫描
  - 交付物：测试或 lint helper
  - 检查项：共享 runtime/repository 代码不得新增 `INSERT OR IGNORE`、`ON CONFLICT`、`datetime('now')`、`PRAGMA`、`sqlite_master`
  - 验收标准：允许列表仅包含 SQLite store、SQLite schema tests、SQLite template validator、明确注释的 SQLite-only 文件

- [ ] 任务 6.8：补齐 typecheck/lint 验证
  - 交付物：最终验证记录
  - 命令：`make typecheck`、`make lint`
  - 验收标准：新增 store、config、tests 类型与 lint 均通过

## 七、文档与运维说明

- [ ] 任务 7.1：更新 agent guide
  - 交付物：[`docs/agent-guide.md`](/Users/lichengxiang/source/oss/marivo/docs/agent-guide.md)
  - 关键内容：metadata 后端选择、SQLite 本地默认、MySQL 生产路径、禁止直接写 metadata DB 的边界
  - 验收标准：agent 不会把 MySQL metadata 误当 execution engine 或 source adapter

- [ ] 任务 7.2：更新配置示例
  - 交付物：`marivo.example.yaml` 或相关 quickstart
  - 范围：SQLite 示例、MySQL 示例、敏感字段推荐通过环境变量或 secret 注入
  - 验收标准：示例可被配置模型解析，且不包含真实密码

- [ ] 任务 7.3：更新 API/运行文档
  - 交付物：metadata runtime docs
  - 关键内容：`metadata.engine` 可选值、MySQL fresh-init 流程、失败面、重建策略
  - 验收标准：operator 能按文档创建空库、配置 Marivo、启动并验证 `/health`

- [ ] 任务 7.4：补充 MySQL 权限与备份建议
  - 交付物：operator runbook
  - 最小内容：建库、建用户、授权、备份、恢复、连接池 sizing、常见错误排查
  - 验收标准：生产部署前置条件清晰，不依赖开发者阅读源码

- [ ] 任务 7.5：记录 non-goals 与后续扩展入口
  - 交付物：decision record
  - 范围：迁移工具、online migration、PostgreSQL、schema migration framework
  - 验收标准：后续需求不会被误认为已在 v1 中承诺

## 验收矩阵

### 全局验收

- [ ] `make test TESTS='tests/test_storage.py tests/test_config.py'` 通过。
- [ ] MySQL 集成测试在未配置 MySQL 时稳定 skip，在配置 MySQL DSN 时通过。
- [ ] `make typecheck` 通过。
- [ ] `make lint` 通过。
- [ ] 静态扫描能阻止共享 repository/runtime 中新增 SQLite 专用 SQL。
- [ ] MySQL 空库启动后，`metadata_store.initialize()` 能创建完整 schema。
- [ ] MySQL 后端可完成 session create、step/artifact commit、finding/proposition idempotent insert、step metadata upsert。
- [ ] 异常注入能证明 MySQL 多语句事务 rollback 生效。
- [ ] 文档说明 Fresh-init v1，不承诺 SQLite -> MySQL 迁移。

### 关键风险与对应验收

| 风险 | 防护任务 | 验收方式 |
| --- | --- | --- |
| DDL 注释声称跨库但实际不可执行 | 3.1、3.2、3.3 | MySQL 空库 initialize 集成测试 |
| 上层继续新增 SQLite 专用 SQL | 2.6、6.7 | 静态扫描测试 |
| MySQL 事务半提交 | 4.3、5.4、6.5 | 失败注入 rollback 测试 |
| 测试模板掩盖 schema drift | 6.1 | SQLite 模板版本和 shape 校验 |
| 密码泄露到日志/API | 4.6、7.2 | 日志/error redaction 测试 |
| 把 metadata MySQL 与 source/engine 混淆 | 7.1、7.3 | 文档术语检查 |

## 默认假设

- 本文只规划 MySQL metadata，不规划 MySQL source adapter 或 MySQL execution engine。
- MySQL 首版按生产可用标准实现，但只支持 fresh-init 空库。
- SQLite 保留为本地开发和大多数单元测试默认 backend。
- metadata schema 仍是 Marivo control-plane schema，不改变 typed intent、source/engine/mapping 的外部业务 contract。
- 若未来需要 SQLite -> MySQL 迁移，应另起独立计划，包含导出、导入、校验、停机窗口、回滚与审计。
