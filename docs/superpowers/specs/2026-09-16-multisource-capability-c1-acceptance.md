# C1：统一列依赖及列注释验收

日期：2026-09-16。状态：C1 已实现并通过源码、六后端真实旅程及约定回归验收。

对应 [C1 实施计划](2026-09-16-multisource-capability-c1-implementation-plan.md)、[C0 基线](2026-09-16-multisource-capability-c0-acceptance.md) 和 [总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)。本记录只覆盖 C1，不更新 C0 历史观察，不表示 C2–C10 已完成。

## 1. 工作区与实现

代码基线为 `7ddbe940de5c379f6944182114a7278ed61ef368`；本次实现仍在工作区，未提交、推送或发布。开始时已有的两份总计划修改及两份 C0 未跟踪文档保持为前序工作。没有修改公共导出、Store 格式、依赖、packaged skills 或 AGENTS.md。

- `compiler/source_dependencies.py` 的 `SourceDependencies`、`EntitySourceDependency`、`SourceColumnDependency` 保存精确 owner、Entity/源绑定及 logical→physical 列事实。`normalize.py` 共享实体/路径遍历，收集隐藏组件、身份、关系键、版本和 Event 依赖。
- scalar 准入、Runtime schema 请求/校验、六个 schema adapter 和 compiler 源投影消费同一依赖分析。移除共享 adapter 的全局 `_declared_columns`。必要列之外的元数据不会进入物理类型解析、collation/存储值检查；关系级限制保持。
- `_Compiler` 的全列输入守卫及 `_spine` 的全列补充连接判断同步改为必要列。独立编译仍验证必要列和类型，不能用删除守卫绕过校验。
- sidecar 捕获表达式语法快照，按参数位置解析列访问，不调用用户函数探测依赖；未知表达式失败，空列摘要不作为纯常量证明。已有 direct-column 与合法 Event 路径保持。
- `SourceSchemaError` 区分必要列缺失、物理类型不支持和类型不匹配，保留 Entity、datasource、关系、逻辑/物理列及声明/实际类型。静态类型拒绝也标明必要列归属。
- PostgreSQL datasource inspection 使用解析关系的 `to_regclass`/`col_description` 补齐列 comment；未设置和不可读有不同结果。注释保持为检视证据，不进入执行依赖或业务口径。

实际内部实现比计划更小：依赖在 Runtime 源步骤开始时冻结并显式传入，不新增 `SourceStep` 持久字段；column 用途用五个内部类别表达，不建立逐算子的第二份注册表。共享测试 helper 只负责独立 SQL 捕获，单模块建表逻辑留在其测试拥有者。

## 2. 新增证据

[test_lazy_source_dependencies.py](../../../tests/test_lazy_source_dependencies.py) 与 [test_lazy_source_schema.py](../../../tests/test_lazy_source_schema.py) 的定向运行：**32 passed**。覆盖六后端未使用声明复杂列放行、隐藏 weight 保留、精确关系/别名、同物理表双 Entity、跨 schema 同名表、错误请求关系、跨 owner 拒绝、版本列、表达式参数位置、未知表达式，以及每个 adapter 在必要类型解析前跳过无关物理列。必要类型解析失败逐后端验证结构化原因与 Entity/列。

[test_lazy_source_dependency_runtime.py](../../../tests/test_lazy_source_dependency_runtime.py) 的真实旅程如下。管理员仅建立/清理 UUID 命名的专用表；远程 Dataset 使用既有 reader，临时 fixture 没有进入项目声明。SQLite/DuckDB 文件位于 pytest 临时目录。

| 后端 / 当前物理范围 | 新增真实成功证据 | 结果 |
| --- | --- | --- |
| DuckDB 普通表 | 未使用声明 array、未声明 array、源别名、同名列跨关系、必要缺列/错类型/重复身份 | 通过 |
| SQLite main 普通表 | 未使用声明复杂列对应物理 BLOB、未声明 BLOB、跨关系同名列、空输出仍检查必要列/身份 | 通过；不激活 BLOB 计算或 VARCHAR 类型 |
| PostgreSQL 普通表 | 未使用声明复杂列对应 JSONB、未声明 JSONB、跨关系同名列、实际 reader 列 comment | 通过 |
| MySQL InnoDB | 未使用声明复杂列对应 JSON、未声明 JSON、跨关系同名列 | 通过；文字列仍要求已限定 collation |
| ClickHouse 普通本地 MergeTree | 未使用声明/未声明 Array、跨关系同名列 | 通过；不激活 Distributed 或新必要类型 |
| Trino Iceberg BASE TABLE | 未使用声明/未声明 Array、跨关系同名列 | 通过；非 Iceberg 仍归 C5 |

独立输入三行 `(id, gross, weight)=(1,10,1),(2,20,2),(3,30,1)`，逻辑 `amount` 绑定物理 `gross`。结果 `sum=60`、`count=3`、`mean=20`、`weighted mean=20`；primary 传输 1 行，字节数大于零。必要源列为 `id/gross/weight`。二表旅程 orders 必要列为 `id/customer_id/gross`，customers 为 `id/region`；customers 的同名 `gross` 为无关复杂列，分组独立预期 `a=40, b=20`。

源 SQL 通过 [独立捕获 helper](../../../tests/lazy_source_dependency_fixtures.py) 检查：四个 scalar backend 使用已有 native cursor 捕获，PostgreSQL 捕获普通/服务端 cursor execute，DuckDB 捕获直接提交 driver 的 adapter submit。实际 SELECT 包含必要 `gross`，不包含 `tenant/undocumented`；同一 target 再执行命中已有 Artifact，primary 查询为零。此证据证明源投影，不证明服务器物理扫描裁剪；未改造 C3a 的完整语句角色/计数对账，也不声称观测到了所有 driver 内部动作。

PostgreSQL reader 检视真实返回 `gross: Gross order amount`、未使用 `tenant: Unused source context`，无注释 `weight` 为 `None`。现有 projected inspection 验证 comment 随物理列映射到逻辑别名；列注释不会被当前 Dataset 的依赖裁剪隐藏。

必要缺列、类型漂移及重复身份在最终输出过滤为空时仍失败；原有关系 fanout、版本、原子发布、parts 和冷读由下列专项回归覆盖。源码验收没有构建或安装新 wheel。

## 3. 命令与结果

以下均在本次工作区执行；不同命令之间有重叠，不能将通过数相加为独立案例总数。

| 命令范围 | 最终证据 |
| --- | --- |
| `make test TESTS='tests/test_lazy_source_dependencies.py tests/test_lazy_source_schema.py'` | 32 passed，退出 0 |
| datasource metadata/projected/authoring inspection + Analysis Help/disclosure 定向组 | 166 passed，退出 0；其中后续新增的依赖案例另由上行覆盖 |
| `MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_source_dependency_runtime.py tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py' RUNTIME_WORKERS=1` | 54 passed、3 skipped，退出 0；当时未启用的其他远程后端明确跳过 |
| MySQL methods/runtime 初轮 | 29 个 methods 案例通过；runtime 旧全声明列预期失败，按下节修复后由后续命令通过 |
| ClickHouse methods/runtime 初轮 | 33 个 methods 案例通过；runtime 旧未使用类型拒绝预期失败，按下节修复后由后续命令通过 |
| `MARIVO_MYSQL_ANALYSIS_TEST=1 MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_source_dependency_runtime.py tests/test_lazy_mysql_runtime.py tests/test_lazy_clickhouse_runtime.py' RUNTIME_WORKERS=1` | 80 passed、2 skipped，退出 0；仅两个 Trino 新旅程未在此组运行 |
| `MARIVO_TRINO_ANALYSIS_TEST=1 MARIVO_POSTGRES_ANALYSIS_TEST=1 MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_source_dependency_runtime.py tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1` | 84 passed、2 skipped，退出 0；仅两个 ClickHouse 新旅程未在此组运行 |
| 恢复环境后的 `MARIVO_POSTGRES_ANALYSIS_TEST=1 MARIVO_MYSQL_ANALYSIS_TEST=1 MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_source_dependency_runtime.py' RUNTIME_WORKERS=1` | 17 passed、2 skipped，退出 0；Trino 两例由上一行证明 |
| `make runtime-test TESTS='tests/test_lazy_materialization_execution.py tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py tests/test_lazy_retained_runtime.py tests/test_lazy_temporal_runtime.py tests/test_lazy_event_runtime.py tests/test_lazy_lifecycle_runtime.py tests/test_lazy_candidate_runtime.py tests/test_lazy_driver_runtime.py tests/test_lazy_distinct_runtime.py tests/test_lazy_distribution_runtime.py tests/test_lazy_adapter_runtime_acceptance.py' RUNTIME_WORKERS=1` | 185 passed，退出 0；包含 DuckDB 私有状态、JSON/文件/adapter、temporal/Event/Lifecycle 和 retained 路径 |
| `make typecheck TYPECHECK_TARGETS='tests/lazy_source_dependency_fixtures.py'` | 通过；共享 cursor capture callback 增加位置参数约束以满足实际方法类型 |
| 最终 `make check-agent` | 5091 passed；lint、导入契约、348 个源文件类型检查、API 文档全部通过，退出 0；此后新增的两个纯关系身份案例由 32 项定向组补验 |
| `npm --prefix site run verify:content` | Verified 343 required site files，退出 0 |
| `npm --prefix site run build` | Astro/Sphinx 构建及 postbuild 安装脚本校验通过，退出 0；构建 321 页 |

## 4. 修复过程中的失败与边界

- 旧 scalar 准入测试修改未使用 `weight` 的类型并要求拒绝，与 C1 冲突；改为验证未使用列放行，实际必要类型拒绝仍有独立边界/adapter 测试。
- SQLite/MySQL 的日期损坏、MySQL collation、ClickHouse 不支持物理类型测试原本没有消费被破坏列。现在显式选择其时间范围/维度，继续证明必要列失败；未使用类型放行由新测试覆盖。
- MySQL 大源/小结果校验次数由 5 变为 4：未使用日期不再产生 `engine_check.mysql_dates`。测试同时断言该语句缺席，数值、排序和有界输出预期保持。
- temporal 两项失败在独立干净 `7ddbe940` worktree 复现（2 failed、4 passed）：旧 mock 指向已不存在的 `admission.probe_engine_timezone`；解析失败误期待 `RuntimeError`。分别改为真实 DuckDB adapter timezone 入口和原始 driver 异常。冷读零源访问、失败无发布/无残留断言保持；修复后包含在 185 项通过中。临时基线 worktree 已移除。
- 新 fixture 的 PostgreSQL `DOUBLE` 拼写与 SQLite `VARCHAR` 曾触发各自真实类型边界，已分别修正为 `DOUBLE PRECISION`、`TEXT`；没有因此扩大生产类型支持。

专用 `marivo-multisource` 环境经 status 核实后串行切换；Trino 与 ClickHouse 不并行运行重型验证。结束时恢复开始状态：ClickHouse、MySQL analysis、PostgreSQL analysis 运行，Trino 与其 catalog PostgreSQL 停止。没有操作其他 Colima 项目、启动 MinIO 或执行 release-check。

## 5. 披露与剩余项

已同步 Analysis owner spec、`analysis.actions.execute` native Help 和中英文 latest 工作流说明。C1 不自动激活额外类型/时间方法、计算型 Measure、Linear、新表形态或远程私有状态；上游 load/readiness 仍校验其完整声明契约。packaged skills 没有新增需要变更的陈旧全列限制，本次未编辑。

C2–C10 继续按各自计划推进；安装包来源/hash、最终六后端 wheel 冷进程整体验收归 C10，不能以本记录替代。
