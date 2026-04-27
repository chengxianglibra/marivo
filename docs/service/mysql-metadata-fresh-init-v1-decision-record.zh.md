# MySQL Metadata Fresh-init v1 Decision Record

状态：accepted；适用于 MySQL metadata fresh-init v1。

## 决策

Marivo metadata backend v1 只支持 `sqlite|mysql`。SQLite 继续作为本地开发与测试默认 backend；MySQL 作为生产级共享 metadata control-plane store，支持空库初始化、新实例启动、连接池、事务 rollback、schema preflight 和基础运行读写。

MySQL metadata 不改变 source、engine、mapping 的 HTTP API 管理边界。它不是 MySQL source adapter，也不是 MySQL execution engine。

## Non-goals

v1 明确不承诺：

- SQLite metadata 文件迁移到 MySQL。
- SQLite/MySQL 双写、online migration、online backfill 或无停机切换。
- 对未知 MySQL schema 自动 `ALTER`、`DROP`、repair 或局部补丁。
- PostgreSQL 或其他 metadata backend。
- 通用 schema migration framework。
- 把 source、engine、mapping inventory 放回 `marivo.yaml`。

这些能力如果成为产品需求，必须另起独立计划，覆盖数据导出导入、校验、停机窗口、回滚、审计、权限和 operator runbook。

## 后续扩展入口

- SQLite -> MySQL 迁移工具：独立迁移计划，要求离线导出、导入、校验和回滚方案。
- MySQL schema migration framework：独立 schema 版本计划，要求向前/向后兼容策略和 preflight 语义重审。
- PostgreSQL backend：独立 backend 支持计划，不能复用当前 `sqlite|mysql` 文档中的已实现承诺。
- Online migration 或 double-write：独立运行切换计划，要求一致性、幂等、观测和降级策略。

在这些计划落地前，文档、配置示例和错误信息只应声明 `sqlite|mysql`，且 MySQL 路径只按 fresh-init v1 运维。
