# C3b 补充验收：Trino 解析时间轴

日期：2026-09-23。此记录补充 [2026-09-19 C3b 验收](2026-09-19-multisource-capability-c3b-acceptance.md) 中 Trino 未验收的单元，不改写当时的历史结论。

## 准入结论

Trino 的 `parsed_time_axes` 准入已通过真实源端执行：`strptime` 日期轴、带时间的解析轴，以及同 Entity civil date 前缀的复合小时轴均可进入类型化 `observe`。原有 epoch、timestamp validity、语义日历和非法小时前缀拒绝仍由无 I/O 准入测试钉定。公共 API 签名未变。

## 执行证据

- 用仓库脚本启动本地 Trino 后，运行 `MARIVO_TRINO_ANALYSIS_TEST=1 .venv/bin/pytest -m runtime -n 0 -q --tb=short --maxfail=5 tests/test_lazy_temporal_backend_runtime.py -k trino`：**32 passed，170 deselected**。其中解析轴用例覆盖日期型、带时间型、上海时区换日、`%f`、NULL 坐标、复合小时前缀、非法小时值，以及纽约 DST gap/fold 拒绝。运行测试按 UUID 创建源表，并在夹具退出时删除。
- 一个类型化 `observe` 提交的 Trino 主查询含 `CAST(DATE_PARSE("t9"."day", '%Y-%m-%d %T') AS TIMESTAMP(6))`，随后使用 `WITH_TIMEZONE(..., 'UTC')` 和 `AT_TIMEZONE(..., 'Asia/Shanghai')`。适配器回执中该主查询为 `succeeded`，`primary_queries=1`；输入 `2026-07-01 15:59:00`、`2026-07-01 16:01:00` 分别落在上海的 7 月 1 日、2 日，Metric 值为 2、3。这是适配器实际提交的 SQL 摘录，不把编译输出当作执行证据。
- 不能按 `%Y%m%d` 解析的 `2026070x` 在 Trino 抛 `TrinoUserError(INVALID_FUNCTION_ARGUMENT)`；对应测试确认 `primary_queries=0` 且没有 Parquet 结果发布。非法复合小时值由 `temporal.hour_range` 结构化拒绝，未发布结果。
- `%f` 已按用户确认保留准入。实际 Trino 探针 `SELECT date_parse('2026-07-01 12:34:56.123456', '%Y-%m-%d %H:%i:%s.%f'), typeof(date_parse('2026-07-01 12:34:56.123456', '%Y-%m-%d %H:%i:%s.%f'))` 返回 `2026-07-01 12:34:56.123000`、`timestamp(3)`。类型化 `observe` 的 `%f` 用例也成功提交含 `DATE_PARSE` 的查询。此能力接受 datasource 原生的毫秒精度；`TIMESTAMP(6)` cast 不恢复被源端解析器丢弃的位数。
- 输入 NULL 与 `20260701` 的日期轴用例在已发布结果中分别保留 NULL 桶（值 2）和 7 月 1 日桶（值 3）。

## 其他检查与服务状态

- 无 I/O 编译和准入定向测试通过；`make check-agent`：**5688 passed，4 skipped**，并通过 lint、typing 和 API 文档阶段。
- `npm --prefix site run verify:content`：343 个必需文件通过；`npm --prefix site run build`：321 页构建及安装脚本校验通过；`git diff --check` 通过。
- 验收前 ClickHouse、MySQL analysis、PostgreSQL analysis 正在运行，Trino 已停止。按本次授权停止这些 Marivo 测试服务并启动 Trino；验收结束后停止 Trino，未恢复先前服务。最终 `manage.sh status trino` 显示所有 Marivo multisource 容器均已停止。
- 本次未运行完整 Runtime、`release-check`，也未进行发布或推送；这些检查不构成发布验收。
