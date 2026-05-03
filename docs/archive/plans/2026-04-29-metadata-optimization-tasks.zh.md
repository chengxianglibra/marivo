# Metadata 表结构优化 — 任务清单

日期：2026-04-29
来源：`plan/2026-04-28-mysql-metadata-table-optimization.zh.md`
总表数变化：46 → 44（若 governance_events 迁移至日志 → 43）
前提：服务未上线，无已有部署，不考虑兼容性和 migration，DDL 变更直接修改源定义。

## 任务依赖图

```
T01 ─┐
T02 ─┤
T03 ─┼─► T04 ─┐
              └─► T05 ─► T06 ─┐
                           T07 ─┘─► T08 ─► T09 ─► T10 ─► T11 ─► T12
                                                      T20 ─┐ T21 ─┘
               T13 ─┐ T14 ─┐ T15 ─┐ T16 ─┐ T17 ─┐ T18 ─┐ T19 ─┐
                                                              └─►（独立）
               T23 ─┐ T24 ─┐ T25 ─┐ T26 ─┐ T27 ─┐
                                                    └─►（独立）
```

---

## Phase 0 — 基础清理 + MySQL 初始化外部化

### T01 删除 plans 废弃表

**前置**：无
**后端**：SQLite + MySQL
**变更文件**：`app/storage/schema.py`

| 步骤 | 操作 |
|---|---|
| 1 | 从 `METADATA_DDL` 中移除 plans 的 `CREATE TABLE IF NOT EXISTS plans (...)` 定义 |
| 2 | 从 `expected_metadata_tables` 列表中移除 `"plans"` |
| 3 | 确认 `metadata_ddl_fingerprint()` 自动更新 |

**验收**：
- `make test` 通过
- SQLite 空库 initialize 后 `SELECT name FROM sqlite_master WHERE type='table'` 不含 plans
- MySQL 空库 initialize 后 `SHOW TABLES` 不含 plans

---

### T02 移除 SQLite legacy migration 代码

**前置**：无
**后端**：SQLite only
**变更文件**：`app/storage/sqlite_metadata.py`

**说明**：服务未上线，不存在已有部署，无需保留任何 legacy 迁移路径。

| 步骤 | 操作 |
|---|---|
| 1 | 删除 `_upgrade_legacy_schema()` 函数及其全部迁移分支 |
| 2 | 简化 `SQLiteMetadataStore.initialize()`：移除 legacy 升级调用，仅保留 fresh-init + schema 校验 |
| 3 | 移除 `_upgrade_legacy_schema()` 相关的测试用例 |

**验收**：
- `make test` 通过
- SQLite 空库 fresh-init 正常
- `_upgrade_legacy_schema` 符号不再存在于 `sqlite_metadata.py`

---

### T03 抽取 MySQL DDL 初始化为独立脚本

**前置**：无
**后端**：MySQL only
**变更文件**：`scripts/metadata/init_mysql_schema.py`（新增）

**背景**：多节点共用 MySQL 时，`initialize()` 中 DDL 创建路径存在竞态（无 advisory lock，并发初始化会导致 duplicate index 错误）。将初始化抽取为外部脚本，服务启动仅校验 schema 状态，schema 变更依赖外部数据库变更流程。

| 步骤 | 操作 |
|---|---|
| 1 | 创建 `scripts/metadata/` 目录 |
| 2 | 创建 `scripts/metadata/init_mysql_schema.py`：读取 `MYSQL_METADATA_DDL`，在空库上执行完整 DDL + 写入 marker |
| 3 | 脚本执行前获取 `GET_LOCK('marivo_metadata_init', 30)` 防止并发执行 |
| 4 | 脚本支持 `--dry-run` 模式：输出将要执行的 DDL，不实际执行 |
| 5 | 脚本支持 `--check` 模式：仅校验 schema 状态，等价于 `evaluate_metadata_schema_state()` |
| 6 | 确保脚本可独立运行（不依赖 Marivo 服务进程） |

**验收**：
- `python scripts/metadata/init_mysql_schema.py --check` 在空库上返回非零退出码
- `python scripts/metadata/init_mysql_schema.py` 在空库上创建完整 schema + marker
- 两个终端同时运行脚本，第二个等待 advisory lock 而非崩溃
- `--dry-run` 输出与实际执行 DDL 一致

---

### T04 重构 MySQLMetadataStore.initialize() 为校验模式

**前置**：T03
**后端**：MySQL only
**变更文件**：`app/storage/mysql_metadata.py`

| 步骤 | 操作 |
|---|---|
| 1 | 从 `initialize()` 中移除 DDL 创建路径（`state == "empty"` 分支） |
| 2 | `initialize()` 仅保留校验逻辑：调用 `evaluate_metadata_schema_state()`，若返回 `"current"` 则通过，否则 raise RuntimeError 并输出可操作的错误提示（提示使用 `scripts/metadata/init_mysql_schema.py`） |
| 3 | 移除 `_assert_mysql_environment()` 中与 DDL 创建相关的逻辑（若有） |
| 4 | 确保 `_assert_current_schema_shape()` 在校验模式下仍正常工作 |

**验收**：
- 空 MySQL 库启动服务时 `initialize()` 抛 RuntimeError，提示运行初始化脚本
- 已有当前 schema 的 MySQL 库启动服务时 `initialize()` 正常通过
- `make test` 通过（测试中 MySQL 初始化改用 init 脚本或直接调用 DDL）

---

## Phase 1 — step_metadata 合并到 steps

### T05 修改 DDL：steps 增列 + 删除 step_metadata

**前置**：T01、T02、T04
**后端**：SQLite + MySQL
**变更文件**：`app/storage/schema.py`

**说明**：无已有部署，直接修改 DDL 源定义，无需 migration 脚本。

| 步骤 | 操作 |
|---|---|
| 1 | 在 `METADATA_DDL` 的 steps 表定义中追加 3 列：`metadata_kind TEXT`、`semantic_snapshot_json TEXT`、`updated_at TEXT NOT NULL DEFAULT (datetime('now'))` |
| 2 | 从 `METADATA_DDL` 中移除 step_metadata 的 `CREATE TABLE` 定义 |
| 3 | 从 `expected_metadata_tables` 中移除 `"step_metadata"` |
| 4 | 确认 `_mysql_table_ddl()` 正确生成 MySQL DDL（steps 含新列，无 step_metadata 表） |

**验收**：
- `MYSQL_METADATA_DDL` 输出中 steps 表含 3 个新列
- `MYSQL_METADATA_DDL` 输出中无 step_metadata 表
- `make test` 通过（此时 step_metadata 相关测试会失败，在 T08 中修复）

---

### T06 删除 StepMetadataRepository，适配 service.py

**前置**：T05
**后端**：SQLite + MySQL
**变更文件**：`app/storage/step_metadata_repository.py`（删除）、`app/service.py`

| 步骤 | 操作 |
|---|---|
| 1 | 删除 `app/storage/step_metadata_repository.py` |
| 2 | 在 `service.py` 中移除 `StepMetadataRepository` 的 import 和实例化 |
| 3 | 修改 `_insert_step()`：将 `metadata_kind`、`semantic_snapshot_json` 作为 steps INSERT 的列直接写入，而非调用 `_step_metadata_repo.upsert()` |
| 4 | 修改 `_insert_step()` 的参数签名：接收 `semantic_metadata: dict[str, Any] | None` 并在 INSERT 时解包 |
| 5 | 修改 `_reset_session_outputs()` 和 `_delete_step_outputs()`：确认 steps CASCADE 删除覆盖语义元数据列，无需额外清理 |

**验收**：
- `make typecheck` 通过（无 `StepMetadataRepository` 悬空引用）
- `_insert_step()` 单次 INSERT 写入 steps 行（含 metadata_kind、semantic_snapshot_json）
- 无 step_metadata 表的引用残留

---

### T07 适配 proposition_seeding_run.py

**前置**：T05
**后端**：SQLite + MySQL
**变更文件**：`app/evidence_engine/proposition_seeding_run.py`

| 步骤 | 操作 |
|---|---|
| 1 | 移除 `StepMetadataRepository` 的 import 和构造函数注入 |
| 2 | 修改 `get_step_semantic_metadata()`：直接查 steps 表的 `metadata_kind`、`semantic_snapshot_json` 列，不再调用 `_step_metadata_repo.get()` |
| 3 | 验证返回结构（`metadata_kind`、`semantic_snapshot`）与原有接口一致 |

**验收**：
- `make typecheck` 通过
- `get_step_semantic_metadata()` 返回值结构与原有一致

---

### T08 重写 step_metadata 相关测试

**前置**：T06、T07
**后端**：SQLite + MySQL
**变更文件**：`tests/test_step_metadata.py`（重写）、`tests/shared_fixtures.py`、其他引用 step_metadata 的测试

| 步骤 | 操作 |
|---|---|
| 1 | 重写 `test_step_metadata.py` → `test_steps_semantic_metadata.py`：测试 steps 表的 metadata_kind / semantic_snapshot_json 列读写 |
| 2 | 更新 `tests/shared_fixtures.py` 中 `_build_metadata_template()`：移除 step_metadata 表引用 |
| 3 | 修复 `test_proposition_seeding_run.py`：验证 `get_step_semantic_metadata()` 从 steps 表读取 |
| 4 | 修复 `test_semantic_typed_end_to_end.py`：验证步骤语义元数据写入 steps |
| 5 | 修复 `test_mysql_metadata_ddl.py`：验证 MYSQL_METADATA_DDL 中 steps 含新列、无 step_metadata |
| 6 | 修复 `test_mysql_metadata_integration.py`：验证 MySQL 空库 steps 含新列 |
| 7 | `make test` 全量通过 |

**验收**：
- `make test` 全量通过
- `make typecheck` 通过
- 无 step_metadata 表名或 `StepMetadataRepository` 引用残留

---

## Phase 2 — 索引 / FK 补齐

### T09 steps.session_id 补 FK + CASCADE

**前置**：T08
**后端**：SQLite + MySQL
**变更文件**：`app/storage/schema.py`

| 步骤 | 操作 |
|---|---|
| 1 | 在 `METADATA_DDL` 的 steps 表定义中，`session_id` 行补 `REFERENCES sessions(session_id) ON DELETE CASCADE` |
| 2 | 确认 `_mysql_table_line()` 自动从 SQLite DDL 提取 FK 并生成 `ALTER TABLE` |

**验收**：
- MySQL 空库 initialize 后 `information_schema.REFERENTIAL_CONSTRAINTS` 包含 `fk_steps_session_id`
- 删除 session 后 steps 自动清除
- `make test` 通过

---

### T10 artifacts.session_id / step_id 补 FK

**前置**：T08
**后端**：SQLite + MySQL
**变更文件**：`app/storage/schema.py`

| 步骤 | 操作 |
|---|---|
| 1 | 在 `METADATA_DDL` 的 artifacts 表定义中，`session_id` 行补 `REFERENCES sessions(session_id) ON DELETE CASCADE` |
| 2 | `step_id` 行补 `REFERENCES steps(step_id) ON DELETE CASCADE` |

**验收**：
- MySQL 空库 initialize 后 artifacts 有两个新 FK
- 删除 session 后 artifacts 及下游 findings/propositions 级联清除
- `make test` 通过

---

### T11 sync_jobs + approval_requests 补索引

**前置**：T08
**后端**：SQLite + MySQL
**变更文件**：`app/storage/schema.py`

| 步骤 | 操作 |
|---|---|
| 1 | 在 `METADATA_DDL` 末尾追加 `CREATE INDEX IF NOT EXISTS idx_sync_jobs_source_id ON sync_jobs(source_id)` |
| 2 | 追加 `CREATE INDEX IF NOT EXISTS idx_approval_requests_session_id ON approval_requests(session_id)` |

**验收**：
- MySQL `EXPLAIN` 显示按 source_id 查 sync_jobs 走索引
- MySQL `EXPLAIN` 显示按 session_id 查 approval_requests 走索引
- `make test` 通过

---

### T12 governance_events 补索引

**前置**：T08
**后端**：SQLite + MySQL
**变更文件**：`app/storage/schema.py`

| 步骤 | 操作 |
|---|---|
| 1 | 在 `METADATA_DDL` 末尾追加 3 个索引： |
| | `idx_governance_events_session_type_created ON governance_events(session_id, event_type, created_at DESC)` |
| | `idx_governance_events_subject ON governance_events(subject_type, subject_id)` |
| | `idx_governance_events_created ON governance_events(created_at DESC)` |

**验收**：
- MySQL `EXPLAIN` 确认 3 个索引各自覆盖对应查询模式
- `make test` 通过

---

## Phase 3 — MySQL 专项优化 + 并发安全

### T13 updated_at 补 ON UPDATE CURRENT_TIMESTAMP(6)

**前置**：无（独立）
**后端**：MySQL only
**变更文件**：`app/storage/schema.py`

| 步骤 | 操作 |
|---|---|
| 1 | 在 `_mysql_table_ddl()` 函数末尾、return 前，添加后处理替换： |
| | `table_sql.replace("DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)",` |
| | `"DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")` |
| 2 | 确认仅替换 `updated_at` 列（其他 `created_at` 列不含此模式） |

**验收**：
- MySQL 下 UPDATE 一行后 `updated_at` 自动刷新
- SQLite DDL 不受影响
- `make test` 通过

---

### T14 连接池 stale 连接健康检查

**前置**：无（独立）
**后端**：MySQL only
**变更文件**：`app/storage/mysql_metadata.py`

| 步骤 | 操作 |
|---|---|
| 1 | 在 `MySQLMetadataStore` 中新增 `_is_connection_alive(con)` 方法 |
| 2 | 修改 `_acquire_connection()`：pool 取出连接后调用 `_is_connection_alive()`，失效则关闭并重新创建 |
| 3 | 编写测试：mock 连接失效场景，验证不抛 `server has gone away` |

**验收**：
- 模拟 MySQL 断开空闲连接后，下一次 metadata 操作正常执行
- 活跃连接不触发额外 `SELECT 1` 开销（或开销可接受）
- `make test` 通过

---

### T15 sync_jobs 并发触发保护

**前置**：无（独立）
**后端**：SQLite + MySQL
**变更文件**：`app/registry/sync_runtime.py`

**背景**：`trigger_sync()` 无去重保护，同一 `source_id` 的两个并发调用会创建两条并行 sync_job。并发同步对 `source_objects` 的 SELECT-then-INSERT 去重逻辑存在竞态，可产生重复行。

| 步骤 | 操作 |
|---|---|
| 1 | 在 `trigger_sync()` 中插入前检查：`SELECT 1 FROM sync_jobs WHERE source_id = ? AND status = 'running'` |
| 2 | 若已存在运行中的同步任务，raise 或返回已有 job_id 而非创建新任务 |
| 3 | 编写测试：并发调用 `trigger_sync()` 同一 source_id，验证只创建一条 sync_job |

**验收**：
- 同一 source_id 并发 `trigger_sync()` 不创建重复 sync_job
- 不同 source_id 的并发触发互不影响
- `make test` 通过

---

### T16 source_objects UNIQUE 约束 + 并发写入安全

**前置**：无（独立）
**后端**：SQLite + MySQL
**变更文件**：`app/storage/schema.py`、`app/registry/sync_runtime.py`

**背景**：`source_objects` 表无 `(source_id, object_type, fqn)` UNIQUE 约束，`_upsert_object()` 用应用层 SELECT-then-INSERT 去重，多节点并发写入时可能产生重复行。

| 步骤 | 操作 |
|---|---|
| 1 | 在 `METADATA_DDL` 的 source_objects 定义后追加 `CREATE UNIQUE INDEX IF NOT EXISTS idx_source_objects_dedup ON source_objects(source_id, object_type, fqn)` |
| 2 | 重构 `_upsert_object()`：用 `INSERT ... ON DUPLICATE KEY UPDATE`（MySQL）/ `INSERT OR REPLACE`（SQLite）替代 SELECT-then-INSERT |
| 3 | 验证 `sync_selections` 等关联表的写入不受影响 |

**验收**：
- 并发同步同一 source 不产生重复 source_objects 行
- 现有 sync 流程正常工作
- MySQL 和 SQLite 行为一致
- `make test` 通过

---

### T17 死锁/冲突重试机制

**前置**：无（独立）
**后端**：MySQL only
**变更文件**：`app/storage/mysql_metadata.py`

**背景**：PyMySQL 默认 REPEATABLE READ + InnoDB gap lock，多节点并发写时死锁概率上升。当前 `connect()` context manager 仅 rollback + raise，无重试。

| 步骤 | 操作 |
|---|---|
| 1 | 在 `MySQLMetadataStore` 中新增 `_execute_with_retry(fn, *args, max_retries=3)` 方法 |
| 2 | 捕获 `pymysql.err.OperationalError` 错误码 1213（Deadlock found）和 1205（Lock wait timeout exceeded），执行指数退避重试 |
| 3 | 在 `execute()` 和 `upsert_by_key()` 等公共写入方法中调用 `_execute_with_retry` |
| 4 | 不对 DDL 操作重试（DDL 失败通常不可重试） |

**验收**：
- 模拟死锁场景后，操作在 2-3 次重试内成功
- 非 deadlock 类错误不触发重试
- 重试延迟不超过 1 秒（3 次，指数退避）
- `make test` 通过

---

### T18 Job 状态更新 CAS 保护

**前置**：无（独立）
**后端**：SQLite + MySQL
**变更文件**：`app/storage/repositories.py`

**背景**：`mark_completed()` / `mark_failed()` 是裸 UPDATE，无 WHERE status='running' 保护。多节点下旧状态更新可覆盖新状态。

| 步骤 | 操作 |
|---|---|
| 1 | 修改 `mark_running()`：`UPDATE jobs SET status='running' WHERE job_id=? AND status='pending'`，检查 affected rows |
| 2 | 修改 `mark_completed()`：`UPDATE jobs SET status='completed' WHERE job_id=? AND status='running'`，检查 affected rows |
| 3 | 修改 `mark_failed()`：同理，`WHERE status='running'` |
| 4 | affected rows == 0 时 raise 或返回状态冲突错误 |

**验收**：
- 已完成的 job 被 mark_completed 时不产生无效状态转换
- 多节点对同一 job 的状态更新不会互相覆盖
- `make test` 通过

---

### T19 显式设置连接隔离级别

**前置**：无（独立）
**后端**：MySQL only
**变更文件**：`app/storage/mysql_metadata.py`

**背景**：当前依赖 PyMySQL 默认 REPEATABLE READ，未显式设置。多节点部署下 REPEATABLE READ 的 gap lock 增加死锁概率。READ COMMITTED 可降低死锁概率且对 metadata 读写模式无影响（metadata 无需要可重复读快照的长事务）。

| 步骤 | 操作 |
|---|---|
| 1 | 在 `_new_connection()` 建立连接后执行 `SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED` |
| 2 | 编写测试：验证新连接的隔离级别为 READ COMMITTED |

**验收**：
- 新连接 `SELECT @@transaction_isolation` 返回 `READ-COMMITTED`
- 现有测试不回归
- `make test` 通过

---

## Phase 4 — 集成测试 + 文档

### T20 全链路 cascade 删除集成测试

**前置**：T09、T10
**后端**：SQLite + MySQL
**变更文件**：`tests/test_cascade_delete.py`（新增）

| 步骤 | 操作 |
|---|---|
| 1 | 编写测试：创建 session → steps → artifacts → findings → propositions → assessments → evidence_gaps → inference_records → action_proposals 完整链路 |
| 2 | 删除 session，验证所有关联表行自动清除 |
| 3 | 分别在 SQLite 和 MySQL 后端运行 |

**验收**：
- 删除 session 后，关联表行数为 0
- SQLite 和 MySQL 行为一致

---

### T21 SQLite / MySQL DDL 一致性验证

**前置**：T08、T11、T12
**后端**：SQLite + MySQL
**变更文件**：`tests/test_metadata_schema_bootstrap.py`（更新）

| 步骤 | 操作 |
|---|---|
| 1 | 更新 `test_metadata_schema_bootstrap.py`：验证 `expected_metadata_tables` 为 44 张表 |
| 2 | 验证 `metadata_ddl_fingerprint("sqlite")` 和 `metadata_ddl_fingerprint("mysql")` 更新后一致 |
| 3 | 验证 MySQL 空库通过 init 脚本初始化后 schema 完整 |

**验收**：
- fingerprint 更新后双后端匹配
- 表数为 44
- `make test` 通过

---

### T22 文档更新

**前置**：T20、T21
**后端**：无
**变更文件**：`spec/service/mysql-metadata/` 下相关文档

| 步骤 | 操作 |
|---|---|
| 1 | 更新 operator-runbook.zh.md：新增初始化脚本操作说明（`init_mysql_schema.py` 用法、--dry-run、--check） |
| 2 | 更新 fresh-init-v1.zh.md：标注服务启动仅校验 schema，初始化需通过脚本完成 |
| 3 | 更新 `expected_metadata_tables` 相关文档（如有） |

**验收**：
- operator-runbook 包含初始化脚本操作步骤
- fresh-init-v1 文档标注了校验模式架构

---

## Phase 5 — P1 项

### T23 source_objects 索引冗余评估

**前置**：无
**后端**：SQLite + MySQL
**变更文件**：`app/storage/schema.py`（可能修改）

| 步骤 | 操作 |
|---|---|
| 1 | 搜集 `source_objects` 的查询模式：grep 所有引用 `source_objects` 的 SQL |
| 2 | 分析是否有仅按 `fqn`（不含 `object_type`）的查询路径 |
| 3 | 若无：从 `METADATA_DDL` 移除 `idx_source_objects_fqn` |
| 4 | 若有：保留，记录理由 |

**验收**：
- 决策有明确依据
- 若删除：MySQL `EXPLAIN` 确认无查询退化

---

### T24 evidence_gaps FK 降级为应用层校验

**前置**：无
**后端**：SQLite + MySQL
**变更文件**：`app/storage/schema.py`、`app/storage/evidence_repositories.py`

| 步骤 | 操作 |
|---|---|
| 1 | 从 `METADATA_DDL` 的 evidence_gaps 定义中移除 `opened_by_inference_record_id` 和 `resolved_by_inference_record_id` 的 `REFERENCES inference_records(...)` |
| 2 | 在 `EvidenceGapRepository.create()` 中增加写入前校验：检查 inference_record_id 存在于 inference_records |
| 3 | 编写测试：写入无效 inference_record_id 时返回明确错误 |

**验收**：
- MySQL DDL 无循环 FK（evidence_gaps ↔ inference_records）
- 写入无效 inference_record_id 时应用层报错
- `make test` 通过

---

### T25 JSON 列补 JSON_VALID() CHECK

**前置**：无
**后端**：MySQL only
**变更文件**：`app/storage/schema.py`

| 步骤 | 操作 |
|---|---|
| 1 | 在 `_mysql_table_line()` 中检测列名以 `_json` 结尾且类型非 LONGTEXT 的列 |
| 2 | 对匹配列追加 `CHECK (JSON_VALID(column_name))` |
| 3 | 编写测试：插入非法 JSON 字符串时 MySQL 拒绝 |

**验收**：
- MySQL `*_json` 列均有 JSON_VALID CHECK
- 插入非法 JSON 被 MySQL 拒绝
- SQLite 不受影响
- `make test` 通过

---

### T26 连接级 innodb_lock_wait_timeout

**前置**：无
**后端**：MySQL only
**变更文件**：`app/storage/mysql_metadata.py`

| 步骤 | 操作 |
|---|---|
| 1 | 在 `_new_connection()` 建立连接后执行 `SET SESSION innodb_lock_wait_timeout = 5` |
| 2 | 编写测试：验证连接的 `innodb_lock_wait_timeout` 为 5 |

**验收**：
- 新连接的 `innodb_lock_wait_timeout` 为 5 秒
- metadata 长事务锁等待超时时返回明确错误
- `make test` 通过

---

### T27 Metadata store 监控指标

**前置**：无
**后端**：MySQL only
**变更文件**：`app/storage/mysql_metadata.py`

| 步骤 | 操作 |
|---|---|
| 1 | 在 `MySQLMetadataStore.__init__()` 中增加 `metrics_callback: Callable[[str, float], None] | None = None` 参数 |
| 2 | 在 `execute_sql()` 中：若 `metrics_callback` 存在，记录 query duration 和 error |
| 3 | 编写测试：mock callback，验证调用参数 |

**验收**：
- 传入 metrics_callback 后，查询执行时 callback 被调用
- callback 参数包含指标名和值
- 不传 callback 时行为不变
- `make test` 通过

---

## 任务总览

| ID | 任务 | Phase | 前置 | 后端 | 关键文件 |
|---|---|---|---|---|---|
| T01 | 删除 plans 废弃表 | 0 | — | 双 | schema.py |
| T02 | 移除 SQLite legacy migration 代码 | 0 | — | SQLite | sqlite_metadata.py |
| T03 | 抽取 MySQL DDL 初始化为独立脚本 | 0 | — | MySQL | scripts/metadata/init_mysql_schema.py |
| T04 | 重构 initialize() 为校验模式 | 0 | T03 | MySQL | mysql_metadata.py |
| T05 | DDL: steps 增列 + 删 step_metadata | 1 | T01,T02,T04 | 双 | schema.py |
| T06 | 删 StepMetadataRepository，适配 service | 1 | T05 | 双 | step_metadata_repository.py, service.py |
| T07 | 适配 proposition_seeding_run | 1 | T05 | 双 | proposition_seeding_run.py |
| T08 | 重写 step_metadata 相关测试 | 1 | T06,T07 | 双 | 多个测试文件 |
| T09 | steps.session_id FK + CASCADE | 2 | T08 | 双 | schema.py |
| T10 | artifacts FK | 2 | T08 | 双 | schema.py |
| T11 | sync_jobs + approval_requests 索引 | 2 | T08 | 双 | schema.py |
| T12 | governance_events 索引 | 2 | T08 | 双 | schema.py |
| T13 | updated_at ON UPDATE | 3 | — | MySQL | schema.py |
| T14 | 连接池健康检查 | 3 | — | MySQL | mysql_metadata.py |
| T15 | sync_jobs 并发触发保护 | 3 | — | 双 | sync_runtime.py |
| T16 | source_objects UNIQUE 约束 | 3 | — | 双 | schema.py, sync_runtime.py |
| T17 | 死锁/冲突重试机制 | 3 | — | MySQL | mysql_metadata.py |
| T18 | Job 状态更新 CAS 保护 | 3 | — | 双 | repositories.py |
| T19 | 显式设置连接隔离级别 | 3 | — | MySQL | mysql_metadata.py |
| T20 | Cascade 删除集成测试 | 4 | T09,T10 | 双 | 新测试文件 |
| T21 | DDL 一致性验证 | 4 | T08,T11,T12 | 双 | 测试文件 |
| T22 | 文档更新 | 4 | T20,T21 | — | spec/ 文档 |
| T23 | source_objects 索引冗余 | 5 | — | 双 | schema.py |
| T24 | evidence_gaps FK 降级 | 5 | — | 双 | schema.py, evidence_repositories.py |
| T25 | JSON_VALID CHECK | 5 | — | MySQL | schema.py |
| T26 | lock_wait_timeout | 5 | — | MySQL | mysql_metadata.py |
| T27 | 监控指标回调 | 5 | — | MySQL | mysql_metadata.py |
