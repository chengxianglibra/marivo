# MySQL Metadata Operator Runbook

本文说明如何以 MySQL 作为 Marivo metadata control-plane store 启动新实例。MySQL metadata 只保存 Marivo 的运行控制面状态；它不是 source adapter，也不是 execution engine。source、engine、mapping 仍通过 HTTP API 管理。

## 前置条件

- MySQL 8.0.16+。
- 目标 database 使用 InnoDB 与 `utf8mb4` 字符集。
- 外键约束可用且不会被实例级配置禁用。
- 应用账号具备目标 schema 下的建表、建索引、读取、写入、更新和删除权限。
- 目标 schema 必须为空，或已经是完整且 fingerprint 匹配的当前 Marivo metadata schema。

MySQL metadata v1 是 fresh-init only。未知非空 schema、缺少 `metadata_schema_marker`、marker backend/version/fingerprint 不匹配、缺关键表、缺索引或缺外键时，启动必须失败。operator 不应通过手工补表、删除 marker、改 fingerprint 或跳过 preflight 的方式绕过失败。

## 建库与授权

生产部署建议由 DBA 或基础设施系统提前创建空库和应用账号：

```sql
CREATE DATABASE marivo_metadata
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_0900_ai_ci;

CREATE USER 'marivo'@'%' IDENTIFIED BY '<managed-secret>';

GRANT SELECT, INSERT, UPDATE, DELETE,
      CREATE, INDEX, REFERENCES
ON marivo_metadata.* TO 'marivo'@'%';
```

这些权限只覆盖 fresh-init 建表/建索引、schema preflight 和运行期 DML，不表示 Marivo 会对未知或历史 schema 执行 schema migration。集成测试账号需要额外具备 `CREATE DATABASE` 与 `DROP DATABASE` 权限，因为测试会创建 `marivo_test_<uuid>` 临时库。

## 配置

本地默认仍使用 SQLite：

```yaml
metadata:
  engine: sqlite
  path: .marivo/metadata.sqlite
```

生产 MySQL 可使用 DSN：

```yaml
metadata:
  engine: mysql
  dsn: mysql+pymysql://marivo:${MARIVO_METADATA_PASSWORD}@mysql.internal:3306/marivo_metadata?connect_timeout=10&pool_size=5&ssl=true
```

也可使用显式字段：

```yaml
metadata:
  engine: mysql
  host: mysql.internal
  port: 3306
  database: marivo_metadata
  user: marivo
  password: ${MARIVO_METADATA_PASSWORD}
  connect_timeout: 10
  pool_size: 5
  ssl: true
```

密码、DSN 和 SSL secret 应由部署系统从 secret store 渲染或注入。不要把真实密码提交到仓库，也不要把完整 DSN 写入工单、日志或测试断言。

## 启动与验证

1. 确认目标 database 为空。
2. 写入 `metadata.engine=mysql` 配置，并设置 `MARIVO_CONFIG` 指向该配置文件。
3. 启动 Marivo 服务。
4. 调用 `/health`，确认服务启动成功。
5. 创建一个 session 并读取 session 列表，验证 metadata store 已可写可读。

首次启动会在空库中创建完整 metadata schema，并写入 `metadata_schema_marker`。后续启动会校验 marker、DDL fingerprint、关键表、索引和外键；只要 preflight 不通过，服务应停在启动失败状态。

## 连接池 sizing

`metadata.pool_size` 是 metadata store 的连接池上限。单实例开发或低并发部署可从 `5` 开始；多 worker 或多实例部署时，应按实例数、worker 数和 MySQL `max_connections` 共同预算。不要用无限连接数掩盖慢查询或事务泄漏；如果连接池耗尽，应先检查请求并发、长事务和 MySQL 连接上限。

`metadata.connect_timeout` 应保持较小的秒级值，避免 MySQL 网络故障时拖慢应用启动和健康检查。

## 备份与恢复

备份应覆盖整个 `marivo_metadata` database，包括 `metadata_schema_marker`。恢复后不要手工修改 marker 或 DDL fingerprint。恢复目标必须满足当前 MySQL 版本、字符集、外键和权限要求，并通过 Marivo startup preflight。

如果恢复到新库，建议先在隔离环境启动 Marivo 验证 `/health`、session create/read/list 和关键业务读写，再切换生产流量。v1 不提供 SQLite -> MySQL 迁移、MySQL schema migration、online backfill 或双写切换工具。

## 常见失败面

| 现象 | 含义 | 处理 |
| --- | --- | --- |
| MySQL 版本低于 8.0.16 | 不满足 CHECK/DDL contract | 升级 MySQL 或使用 SQLite 本地路径 |
| 目标 schema 有未知表 | 不是空库，也不是当前 Marivo schema | 换空库；不要让 Marivo 自动清理 |
| 缺 `metadata_schema_marker` | schema 不是已识别 current schema | 换空库或按备份恢复完整 schema |
| fingerprint mismatch | DDL 与当前版本不一致 | 使用匹配版本恢复；另起 schema migration 计划 |
| 缺索引或外键 | schema shape 不完整 | 从完整备份恢复；不要局部补丁后绕过 preflight |
| 权限不足 | 应用账号无法初始化或读写 metadata | 补齐目标库权限后重启 |
