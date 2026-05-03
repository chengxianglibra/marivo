# Metadata 表结构优化方案（MySQL + SQLite 双后端）

日期：2026-04-28（初版）/ 2026-04-29（扩展 SQLite + 表合并 + 并发安全）
基线：`app/storage/schema.py` 中 46 张 metadata 表
前置文档：`plan/2026-04-25-mysql-metadata-todo-task-list.zh.md`、`spec/service/mysql-metadata/fresh-init-v1.zh.md`
前提：服务未上线，无已有部署，不考虑兼容性和 migration，DDL 变更直接修改源定义。

## 目标

对 **SQLite 和 MySQL 双后端** 的 metadata store 做一次性补齐，涵盖：

1. **表结构简化**：评估并合并部分表，降低 metadata 层的表数量和管理复杂度
2. **索引 / 外键补齐**：确保生产级数据完整性和运行时稳定性
3. **MySQL 初始化外部化**：服务启动仅校验 schema，DDL 创建和变更由外部脚本完成
4. **多节点并发安全**：补齐 MySQL 多节点共用场景下的竞态保护
5. **连接管理 / 时间语义**：MySQL 端专项优化

本文不改变 Semantic 层（12 张）和 Typed Binding 层（9 张）的已有结构设计；这两域已稳定，CHECK 约束完备，不建议调整。

## 表合并评估

### 现状：46 张表的域划分

| 域 | 表数 | 表名 | 状态 |
|---|---|---|---|
| Session/Step | 4 | sessions, steps, step_metadata, artifacts | 可优化 |
| Source/Sync | 5 | sources, source_objects, source_execution_mappings, sync_jobs, sync_selections | 可优化 |
| Semantic 层 | 12 | semantic_entity_contracts, semantic_entity_key_refs, semantic_entity_stable_descriptors, semantic_metric_contracts, semantic_process_objects, semantic_process_exported_dimension_refs, semantic_dimension_contracts, semantic_time_objects, semantic_enum_sets, semantic_enum_set_versions, semantic_enum_set_values, semantic_predicate_contracts | **稳定，不动** |
| Typed Binding 层 | 9 | typed_bindings, binding_imports, carrier_bindings, carrier_field_surfaces, carrier_time_surfaces, field_bindings, time_bindings, join_relations, consumption_policies | **稳定，不动** |
| Compiler | 1 | compiler_compatibility_profiles | 保留 |
| Engine | 1 | engines | 保留 |
| Governance | 4 | policies, quality_rules, approval_requests, governance_events | 评估后保留 |
| Job | 1 | jobs | 保留 |
| Evidence 层 | 7 | findings, propositions, assessments, evidence_gaps, inference_records, action_proposals, proposition_seed_finding_refs | 结构合理，不动 |
| Schema 基础设施 | 1 | metadata_schema_marker | 保留 |
| **废弃** | 1 | **plans** | **零使用，删除** |

**不动域合计**：12 (Semantic) + 9 (Binding) + 7 (Evidence) + 3 (compiler/engines/marker) = 31 张表

### 合并决策

#### plans — 删除

DDL 已定义，但代码库中零 INSERT/SELECT/UPDATE/DELETE。完全废弃。从 `METADATA_DDL` 中移除 plans 定义及相关索引，同步更新 `expected_metadata_tables`。

#### step_metadata → 合并到 steps

step_metadata 是 steps 的 1:1 可选扩展表，PK 即 `step_id`，仅额外存储 3 个有意义的列：
- `metadata_kind`（实际值恒为 `"typed_semantic_snapshot"`，属于伪枚举）
- `semantic_snapshot_json`（大型 JSON，存编译器解析快照）
- `updated_at`

**合并理由**：
- 1:1 关系，无独立查询场景
- 代码中从未 JOIN 两表，始终用两次顺序查询（`SELECT FROM steps` 后 `StepMetadataRepository.get()`）
- 合并后减少 1 张表 + 1 个 repository + 1 个 FK 级联链

**合并方式**：直接在 `METADATA_DDL` 的 steps 表定义中增加 3 列，同时移除 step_metadata 的 CREATE TABLE 定义：

```sql
-- steps 表新增 3 列
metadata_kind          TEXT,                    -- NULL 表示无语义元数据
semantic_snapshot_json TEXT,                    -- NULL 表示无语义元数据
updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
```

无需数据迁移，直接修改 DDL 源定义。

**影响文件**：`schema.py`（DDL）、`step_metadata_repository.py`（删除）、`service.py`（`_insert_step` 适配）、`proposition_seeding_run.py`（`get_step_semantic_metadata` 改查 steps）、测试文件。

**风险**：`semantic_snapshot_json` 是大型 JSON，合并后 steps 行变宽。SQLite 以页面为单位存储，大 JSON 本就跨页，影响有限。MySQL 可通过后续 PAGE COMPRESSION 处理。

#### 不合并的表对

| 表对/组 | 评估 | 结论 |
|---|---|---|
| sync_selections → sources | 可折入但 UNIQUE 约束需退化为应用层，MySQL 多连接下有并发风险 | 保留独立表 |
| jobs + sync_jobs | 不同域（step 执行 vs catalog 同步），不同生命周期 | 不合并 |
| governance_events + approval_requests | 审计日志 vs 状态机，互补关系 | 不合并 |
| policies + quality_rules | 命名相似但域不同，合并需 discriminator + 多 nullable 列 | 不合并 |
| proposition_seed_finding_refs → propositions | 有意 dual-surface 设计，junction 表用于反向查找 | 不合并 |
| source_execution_mappings → sources 或 engines | N:M junction，两端均需独立查询 | 不合并 |
| evidence 层 7 张表 | 管道各阶段有独立身份和生命周期 | 不合并 |

### 合并后表数变化

46 → **44** 张表，净减少 2 张：1 删除 + 1 合并。若 governance_events 迁移至日志系统，最终 **43** 张表。

## 优化项总览

| 优先级 | 类别 | 项数 |
|---|---|---|
| P0 | 上线必须 | 11 |
| P1 | 上线后尽快 | 10 |
| P2 | 未来优化 | 4 |
| — | 日志/指标迁移 | 1 强候选 + 2 可选 |

---

## P0 — 上线必须

### 1. 删除 plans 废弃表

**现状**：`plans` 表在 `METADATA_DDL` 中定义但代码库中零使用。无 INSERT/SELECT/UPDATE/DELETE。

**变更**：从 `METADATA_DDL` 中移除 plans 的 CREATE TABLE 定义。同步更新 `expected_metadata_tables` 列表。

**影响**：无。没有任何代码引用此表。

**验收**：
- SQLite 和 MySQL 空库 initialize 后均不包含 plans 表
- `expected_metadata_tables` 不再包含 plans
- 全量测试通过

### 2. step_metadata 合并到 steps

**现状**：step_metadata 是 steps 的 1:1 可选扩展表，代码中从未 JOIN，始终用两次顺序查询。

**变更**：直接修改 `METADATA_DDL` 源定义，无需数据迁移：

```sql
-- steps 表新增 3 列（直接写入 DDL 定义）
metadata_kind          TEXT,
semantic_snapshot_json TEXT,
updated_at             TEXT NOT NULL DEFAULT (datetime('now'))

-- 同时从 DDL 中删除 step_metadata 的 CREATE TABLE 定义
```

MySQL DDL 由 `_mysql_metadata_ddl()` 自动从 SQLite DDL 生成，需确认 steps 的列定义正确生成。

**影响文件**：
- `app/storage/schema.py` — steps 表增列，删除 step_metadata 定义
- `app/storage/step_metadata_repository.py` — 删除此文件
- `app/service.py` — `_insert_step()` 合并写入，移除 `_step_metadata_repo`
- `app/evidence_engine/proposition_seeding_run.py` — `get_step_semantic_metadata()` 改为直接查 steps 表
- `tests/test_step_metadata.py` — 重写为 steps 语义元数据测试
- `tests/shared_fixtures.py` — 更新 `_build_metadata_template()`

**验收**：
- steps 表包含 metadata_kind、semantic_snapshot_json、updated_at 三列
- step_metadata 表不再存在
- `_insert_step()` 一次性写入 steps 行（含语义元数据列）
- `get_step_semantic_metadata()` 直接从 steps 表读取

### 3. steps.session_id 补 FK + CASCADE

**现状**：`steps.session_id` 无 FK 指向 `sessions(session_id)`；删除 session 后 steps 残留。

**变更**：

```sql
-- SQLite: 在 METADATA_DDL 的 steps 表定义中补 REFERENCES sessions(session_id) ON DELETE CASCADE
-- MySQL: _mysql_table_line() 自动从 SQLite DDL 中提取 FK 并生成 ALTER TABLE
```

**验收**：
- MySQL 空库 initialize 后 `information_schema.REFERENTIAL_CONSTRAINTS` 包含 `fk_steps_session_id`
- 删除 session 后关联 steps 自动清除

### 4. artifacts.session_id / step_id 补 FK

**现状**：`artifacts` 的 `session_id`、`step_id` 无 FK；findings FK 引用 artifacts 但 artifacts 不引用 sessions/steps，形成不对称引用链。

**变更**：

```sql
-- SQLite: 在 DDL 定义中补 REFERENCES ... ON DELETE CASCADE
-- MySQL: 自动生成
```

**验收**：删除 session 后 artifacts 和下游 findings/propositions 均级联清除。

### 5. sync_jobs 补 source_id 索引

**变更**：

```sql
CREATE INDEX idx_sync_jobs_source_id ON sync_jobs(source_id);
```

### 6. approval_requests 补 session_id 索引

**变更**：

```sql
CREATE INDEX idx_approval_requests_session_id ON approval_requests(session_id);
```

### 7. governance_events 补索引

**变更**：

```sql
CREATE INDEX idx_governance_events_session_type_created
  ON governance_events(session_id, event_type, created_at DESC);
CREATE INDEX idx_governance_events_subject
  ON governance_events(subject_type, subject_id);
CREATE INDEX idx_governance_events_created
  ON governance_events(created_at DESC);
```

**理由**：审计表数据量增长快，三个索引分别覆盖"按会话查审计"、"按主体查审计"、"按时间查审计"。

### 8. updated_at 补 ON UPDATE CURRENT_TIMESTAMP(6)

**变更**：在 `_mysql_table_ddl()` 中对 `updated_at` 列做后处理替换：

```python
table_sql = table_sql.replace(
    "updated_at                      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)",
    "updated_at                      DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)",
)
```

**注意**：SQLite 不支持 `ON UPDATE` 语法，此变更仅作用于 MySQL DDL 生成路径。

### 9. 连接池 stale 连接健康检查

**现状**：`MySQLMetadataStore._acquire_connection()` 从 pool 取连接后不验证连接是否存活。

**变更**：在 `_acquire_connection()` 中增加连接可用性验证（`_is_connection_alive`），失效则关闭并创建新连接。

**验收**：模拟 MySQL 断开空闲连接后，下一次 metadata 操作不抛 `server has gone away`。

### 10. SQLite legacy migration 代码移除

**现状**：`sqlite_metadata.py` 中 `_upgrade_legacy_schema()` 包含对 `sources__legacy` 的复杂迁移逻辑。服务未上线，不存在已有部署，无需保留任何 legacy 迁移路径。

**变更**：删除 `_upgrade_legacy_schema()` 函数及其全部迁移分支。简化 `SQLiteMetadataStore.initialize()`：仅保留 fresh-init + schema 校验。

**验收**：`_upgrade_legacy_schema` 符号不再存在于 `sqlite_metadata.py`。

### 11. MySQL DDL 初始化外部化

**现状**：`MySQLMetadataStore.initialize()` 同时承担 DDL 创建和 schema 校验。多节点共用 MySQL 时存在竞态风险：
- 两个节点同时对空库初始化：都读到 `table_names` 为空 → 都执行 DDL → 第二个节点在 `CREATE INDEX` 处失败（MySQL DDL 已去除 `IF NOT EXISTS`）
- TOCTOU 缺口：`_table_names()` 和 `_marker_row()` 非原子，节点 A 创建表但未 commit marker 时，节点 B 判定 schema invalid
- 服务进程不应具备 DDL 权限，schema 变更应走外部数据库变更流程

**变更**：

1. 创建 `scripts/metadata/init_mysql_schema.py`：
   - 读取 `MYSQL_METADATA_DDL`，在空库上执行完整 DDL + 写入 marker
   - 执行前获取 `GET_LOCK('marivo_metadata_init', 30)` 防止并发执行
   - 支持 `--dry-run`（输出 DDL 不执行）和 `--check`（仅校验 schema 状态）

2. 重构 `MySQLMetadataStore.initialize()` 为校验模式：
   - 移除 DDL 创建路径（`state == "empty"` 分支）
   - 仅调用 `evaluate_metadata_schema_state()`，返回 `"current"` 则通过，否则 raise RuntimeError 并提示运行初始化脚本
   - 服务启动不再需要 DDL 权限

**验收**：
- 空 MySQL 库启动服务时 `initialize()` 抛 RuntimeError，提示运行初始化脚本
- 已有当前 schema 的 MySQL 库启动服务时正常通过
- 两个终端同时运行 init 脚本，第二个等待 advisory lock 而非崩溃
- `--check` 模式等价于 `evaluate_metadata_schema_state()`

---

## P1 — 上线后尽快

### 12. source_objects 索引冗余清理

**现状**：`idx_source_objects_fqn` 是 `idx_source_objects_object_type_fqn(object_type, fqn)` 的前缀子集。

**变更**：评估查询模式后决定是否删除 `idx_source_objects_fqn`。

**验收**：`EXPLAIN` 确认删除后无查询退化。

### 13. evidence_gaps FK 降级

**现状**：`evidence_gaps` 的两个 `inference_records` FK 形成循环依赖。

**变更**：移除 FK 约束，在 `EvidenceGapRepository` 中增加写入前校验。

**验收**：MySQL DDL 无循环 FK；写入无效 inference_record_id 时应用层返回明确错误。

### 14. JSON 列补 JSON_VALID() CHECK

**变更**：在 MySQL DDL 生成时，对 `*_json` 列追加 `CHECK (JSON_VALID(column_name))`。SQLite 不支持 `JSON_VALID`，此约束仅作用于 MySQL。

**验收**：插入非法 JSON 字符串时 MySQL 拒绝写入。

### 15. 连接级 innodb_lock_wait_timeout

**变更**：在 `_new_connection()` 建立连接后执行 `SET SESSION innodb_lock_wait_timeout = 5`。

**验收**：metadata 长事务锁等待超时时返回明确错误而非 hang。

### 16. Metadata store 监控指标

**变更**：在 `MySQLMetadataStore` 中增加可选的 metrics 回调。

**验收**：集成 Prometheus 或等价 metrics 系统后可观测 metadata query latency 和错误率。

### 17. sync_jobs 并发触发保护

**现状**：`trigger_sync()` 无去重保护，同一 `source_id` 的两个并发调用会创建两条并行 sync_job。并发同步对 `source_objects` 的 SELECT-then-INSERT 去重逻辑存在竞态，可产生重复行。

**变更**：在 `trigger_sync()` 插入前检查 `SELECT 1 FROM sync_jobs WHERE source_id = ? AND status = 'running'`。若已存在运行中同步任务，raise 或返回已有 job_id。

**验收**：同一 source_id 并发 `trigger_sync()` 不创建重复 sync_job。

### 18. source_objects UNIQUE 约束 + 并发写入安全

**现状**：`source_objects` 表无 `(source_id, object_type, fqn)` UNIQUE 约束，`_upsert_object()` 用应用层 SELECT-then-INSERT 去重，多节点并发写入时可产生重复行。

**变更**：
1. 在 `METADATA_DDL` 中追加 `CREATE UNIQUE INDEX idx_source_objects_dedup ON source_objects(source_id, object_type, fqn)`
2. 重构 `_upsert_object()`：用 `INSERT ... ON DUPLICATE KEY UPDATE`（MySQL）/ `INSERT OR REPLACE`（SQLite）替代 SELECT-then-INSERT

**验收**：并发同步同一 source 不产生重复 source_objects 行。

### 19. 死锁/冲突重试机制

**现状**：PyMySQL 默认 REPEATABLE READ + InnoDB gap lock，多节点并发写时死锁概率上升。当前 `connect()` context manager 仅 rollback + raise，无重试。

**变更**：在 `MySQLMetadataStore` 中新增 `_execute_with_retry(fn, max_retries=3)` 方法，捕获 `OperationalError` 错误码 1213（Deadlock found）和 1205（Lock wait timeout exceeded），执行指数退避重试。

**验收**：模拟死锁场景后，操作在 2-3 次重试内成功。

### 20. Job 状态更新 CAS 保护

**现状**：`mark_completed()` / `mark_failed()` 是裸 UPDATE，无 WHERE status='running' 保护。多节点下旧状态更新可覆盖新状态。

**变更**：修改状态转换 SQL 加 WHERE 条件（如 `WHERE job_id=? AND status='running'`），检查 affected rows，为 0 时 raise 状态冲突错误。

**验收**：已完成的 job 被 mark_completed 时不产生无效状态转换。

### 21. 显式设置连接隔离级别

**现状**：依赖 PyMySQL 默认 REPEATABLE READ，未显式设置。多节点部署下 gap lock 增加死锁概率。

**变更**：在 `_new_connection()` 建立连接后执行 `SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED`。metadata 读写模式无需要可重复读快照的长事务，READ COMMITTED 可降低死锁概率。

**验收**：新连接隔离级别为 READ COMMITTED，现有测试不回归。

---

## 日志/指标迁移评估

### 评估方法

对全部 metadata 表按以下维度判断是否更适合存储在日志/指标系统而非关系数据库：

1. **写入模式**：是否纯 append-only（无 UPDATE/DELETE）？
2. **查询模式**：是否无 JOIN、无跨表引用、仅按标签过滤？
3. **FK 依赖**：是否有其他表通过 FK 引用此表？
4. **API 暴露**：是否有直接 API 端点消费此数据？
5. **管道嵌入度**：是否被核心管道多个模块深度消费？

### 逐表评估

| 表 | Append-only | 无 JOIN | 无 FK 引用 | 无 API 端点 | 低管道嵌入度 | 迁移适配度 |
|---|---|---|---|---|---|---|
| **governance_events** | **是** | **是** | **是** | **是**（无 API 路由） | **是**（仅 2 个消费者） | **强** |
| inference_records | 是 | 是 | **否**（evidence_gaps FK 引用） | 间接（bundle） | 否（7+ 消费者） | 弱 |
| action_proposals | 是 | 是 | 是 | 间接（state/context） | 否（7+ 消费者 + json_extract 查询） | 弱 |
| sync_jobs | 否（有 UPDATE） | 是 | 是 | 是（状态轮询） | 是（2 个消费者） | 中 |
| jobs | 否（4 种 UPDATE） | 是 | 是 | 是（完整 CRUD） | 是（1 个消费者） | 中 |

### governance_events — 推荐迁移至日志系统

**现状**：
- 纯 append-only，零 UPDATE/DELETE
- 无 JOIN，无 FK 被引用
- 无 API 端点暴露（`list_audit_events()` 存在但未接入路由）
- 仅两个消费者：`GovernanceRuntime.check_step()`（写入）、`ApprovalRuntime.get_request_audit_trail()`（读取审计记录）

**迁移方案**：
1. 将 `governance_events` 的写入替换为结构化日志（如 Python `structlog` + 标签 `subject_type`、`subject_id`、`session_id`、`event_type`）
2. 保留 DB 表作为过渡期双写目标，读路径优先走日志系统
3. `get_request_audit_trail()` 改为查询日志系统而非 DB
4. 验证无回归后移除 DB 表

**收益**：
- 减少 1 张无界增长表（governance_events 每次治理检查都写入一行）
- 日志系统天然支持 TTL / 归档，无需额外实现
- 减少 metadata DB 写入量

**风险**：
- 日志系统查询能力弱于 SQL（无结构化过滤），但当前查询模式简单（按 session_id / subject 过滤）
- 审计数据从强一致性存储退化为最终一致性日志，需评估合规要求

### inference_records / action_proposals — 不推荐迁移

- `inference_records` 被 `evidence_gaps` 通过 FK 引用，迁移需同步处理 evidence_gaps，破坏管道完整性
- `action_proposals` 的 `json_extract` 查询是核心管道功能，日志系统无法替代
- 两表均为 API 暴露的状态视图核心组成部分

### sync_jobs / jobs — 可选的混合方案

- 活跃任务（status != completed/failed）保留在 DB，支持状态机变更
- 已完成任务归档到日志/指标系统，减少 DB 无界增长
- 当前规模下收益有限，建议作为 P2 评估

### 迁移后表数变化

如果 governance_events 迁移至日志系统：44 → **43** 张表。

---

## P2 — 未来优化（4 项）

### 22. proposition_seed_finding_refs.role 补 CHECK

```sql
ALTER TABLE proposition_seed_finding_refs
  ADD CONSTRAINT chk_prop_seed_refs_role CHECK (role IN ('primary', 'secondary'));
```

### 23. 大表分区策略

候选表：`findings`、`propositions`。
分区键：`session_id`（LIST）或 `created_at`（RANGE）。
前提：单表数据量超过 1000 万行时评估。

注：`governance_events` 原在候选列表中，但若迁移至日志系统（见"日志/指标迁移评估"），则无需分区。

### 24. 旧数据归档 / TTL

- `sessions` + 关联表按 `created_at` 归档到冷存储
- `governance_events` 若未迁移至日志系统，按 `created_at` 保留 N 天
- `sync_jobs` / `jobs` 已完成任务归档
- 需要应用层归档 API 或定时任务

### 25. FK CASCADE 一致性审计

当前 FK CASCADE 不对称。P0-3/P0-4 修复后应增加一致性测试：删除 session 时，steps → artifacts → findings → propositions → assessments → evidence_gaps → inference_records → action_proposals 全链路级联清除。

---

## 变更影响分析

### DDL 源头变更

所有 DDL 变更统一修改 `app/storage/schema.py` 中的 `METADATA_DDL` 列表。MySQL DDL 由 `_mysql_metadata_ddl()` 自动从 SQLite DDL 生成，因此只需改 SQLite DDL 源头：

- **P0-1**：删除 plans 表定义
- **P0-2**：steps 表增列（metadata_kind, semantic_snapshot_json, updated_at），删除 step_metadata 定义
- **P0-3**：steps 表定义中 `session_id` 行补 `REFERENCES sessions(session_id) ON DELETE CASCADE`
- **P0-4**：artifacts 表定义中 `session_id` 补 `REFERENCES sessions(session_id) ON DELETE CASCADE`，`step_id` 补 `REFERENCES steps(step_id) ON DELETE CASCADE`
- **P0-5 ~ P0-7**：在 `METADATA_DDL` 末尾追加对应 `CREATE INDEX` 语句
- **P0-8**：修改 `_mysql_table_ddl()` 函数
- **P0-9**：修改 `MySQLMetadataStore._acquire_connection()`
- **P0-10**：移除 `sqlite_metadata.py` 中 `_upgrade_legacy_schema()` 函数
- **P0-11**：新增 `scripts/metadata/init_mysql_schema.py`，重构 `initialize()` 为校验模式

### SQLite 端 vs MySQL 端适用性

| 项 | SQLite | MySQL |
|---|---|---|
| P0-1 删除 plans | 适用 | 适用 |
| P0-2 step_metadata 合并 | 适用 | 适用 |
| P0-3 steps FK | 适用 | 适用 |
| P0-4 artifacts FK | 适用 | 适用 |
| P0-5 sync_jobs 索引 | 适用 | 适用 |
| P0-6 approval_requests 索引 | 适用 | 适用 |
| P0-7 governance_events 索引 | 适用 | 适用 |
| P0-8 ON UPDATE CURRENT_TIMESTAMP | **不适用**（SQLite 不支持） | 适用 |
| P0-9 连接池健康检查 | **不适用**（SQLite in-process） | 适用 |
| P0-10 legacy migration 移除 | 适用 | **不适用** |
| P0-11 MySQL 初始化外部化 | **不适用**（SQLite in-process） | 适用 |

### Schema marker 与 fingerprint

所有 DDL 变更会导致 `metadata_ddl_fingerprint()` 变化。由于无已有部署，处理方式简单：

1. 修改 DDL 后 fingerprint 自动更新
2. 用 `scripts/metadata/init_mysql_schema.py` 重新初始化空库即可
3. SQLite 空库 fresh-init 自动使用新 DDL
4. 不需要 migration 或版本升级逻辑

### 测试矩阵更新

- `test_mysql_metadata_ddl.py`：验证新增 FK 和索引出现在 `MYSQL_METADATA_DDL` 输出中
- `test_mysql_metadata_integration.py`：验证 MySQL 空库通过 init 脚本初始化后新 FK 和索引生效
- `test_metadata_schema_bootstrap.py`：验证 `expected_metadata_tables`（44 张表）和 marker fingerprint 更新
- `test_step_metadata.py` → 重写为 `test_steps_semantic_metadata.py`：验证 steps 表包含语义元数据列
- 新增 cascade 删除集成测试：删除 session 后验证全链路级联清除
- 新增 stale 连接恢复测试
- 新增 init 脚本测试：空库 → init 脚本 → 服务 initialize 通过
- 新增 init 脚本并发 advisory lock 测试
- 新增 sync_jobs 并发触发保护测试
- 新增 source_objects UNIQUE 约束并发写入测试
- 新增死锁重试测试

---

## 实施顺序

```
Phase 0 — 基础清理 + MySQL 初始化外部化（2-3 天）
  │
  ├─ P0-1: 删除 plans 表定义
  ├─ P0-10: 移除 _upgrade_legacy_schema()
  ├─ P0-11: 创建 scripts/metadata/init_mysql_schema.py
  ├─ P0-11: 重构 initialize() 为校验模式
  └─ 验证测试通过

Phase 1 — P0-2 step_metadata 合并（2-3 天）
  │
  ├─ 修改 METADATA_DDL：steps 增列，删除 step_metadata 定义
  ├─ 删除 StepMetadataRepository，逻辑迁入 steps 相关代码
  ├─ 修改 service.py _insert_step()
  ├─ 修改 proposition_seeding_run.py
  └─ 更新测试

Phase 2 — P0-3~P0-7 索引/FK 补齐（1-2 天）
  │
  ├─ P0-3: steps.session_id FK + CASCADE
  ├─ P0-4: artifacts FK
  ├─ P0-5: sync_jobs.source_id 索引
  ├─ P0-6: approval_requests.session_id 索引
  ├─ P0-7: governance_events 3 个索引
  └─ fingerprint 自动更新

Phase 3 — P0-8~P0-9 MySQL 专项 + 并发安全（2-3 天）
  │
  ├─ P0-8: updated_at ON UPDATE CURRENT_TIMESTAMP
  ├─ P0-9: 连接池健康检查
  ├─ P1-17: sync_jobs 并发触发保护
  ├─ P1-18: source_objects UNIQUE 约束 + 并发写入安全
  ├─ P1-19: 死锁/冲突重试机制
  ├─ P1-20: Job 状态更新 CAS 保护
  └─ P1-21: 显式设置连接隔离级别

Phase 4 — 集成测试 + 文档更新（1-2 天）
  │
  ├─ 全链路 cascade 删除测试
  ├─ SQLite ↔ MySQL DDL 一致性验证
  ├─ init 脚本端到端测试
  └─ 更新 operator-runbook / fresh-init 文档

Phase 5 — P1 项（约 3-5 天）
  │
  ├─ 索引冗余评估
  ├─ evidence_gaps FK 降级
  ├─ JSON_VALID CHECK
  ├─ lock_wait_timeout
  └─ metrics 回调
```

## Non-goals

- 不做 SQLite → MySQL 数据迁移
- 不做灰度双写或跨后端同步
- 不做 PostgreSQL 扩展
- 不做 online backfill
- 不做 schema migration 框架（服务未上线，无已有部署，DDL 直接修改）
- 不改变 Semantic 层和 Typed Binding 层的已有表结构
- 不合并 sync_selections（UNIQUE 约束退化为应用层的风险不可接受）
- 不合并 policies + quality_rules（域不同，合并复杂度大于收益）
- SQLite initialize 仍在服务进程中执行（SQLite 为 in-process 单节点，无并发风险）
- 不引入分布式锁服务（使用 MySQL advisory lock 即可）
