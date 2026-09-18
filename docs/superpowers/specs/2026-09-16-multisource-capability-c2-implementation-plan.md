# C2：基础标量映射、精确传输与基础方法实施计划

日期：2026-09-16。状态：已实现并验收，见 [C2 验收](2026-09-16-multisource-capability-c2-acceptance.md)；用户已确认普通 timestamp 时间谓词归 C3a。

依据：[C0 实施清单](2026-09-16-multisource-capability-c0-implementation-plan.md)、[C0 目标矩阵](2026-09-16-multisource-capability-c0-acceptance.md)、[C1 验收](2026-09-16-multisource-capability-c1-acceptance.md)、[总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)。

## 1. 基线、边界与完成定义

计划准备时 C1 尚未提交；实施开始时已核实 C1 提交为 `2602989af1680ddf54790e7664ebc03ca912d621`，只有本计划未跟踪。C2 基于该提交实施，保留其六后端依赖契约。

C2 完成 T1 的物理映射、精确传输及逐类型基础方法。沿用 C1 的 `SourceDependencies`，必要列才进入映射和存储检查；上游 datasource/semantic load/readiness 的契约仍须贯通。DuckDB 保留已有能力，并作为共同 Runtime、Arrow、Parquet 和冷读的回归目标。

完成要求：下述每个正向单元都有真实源执行、独立预期值、类型/值/NULL 核对及冷进程读取证据；所有负向单元在发布前失败；源码门禁和披露同步通过。只通过 mapper 单测、声明准入或 driver 直读不能标为完成。任何必交单元遇到上游或后端约束，先记录具体阻塞并修订计划，不能静默移出目标。

排除：timestamp 时间轴、时间桶、DST、parser、版本时间扩展与统一时区策略/执行记录改造（C3a）；计算型 Measure、Linear、Decimal 组合精度（C4）；Distributed、非 Iceberg 与新表形态/collation（C5）；T3 正向计算、远程私有状态、跨源 join、retained 上传、公共 API/Store 格式变更、依赖升级、packaged skills 编辑、提交/推送/发布与 wheel 验收。C5 的已锁定目标继续保留，不因本阶段样本受限而取消。

## 2. 精确类型和物理输入矩阵

以下为实施契约；最终通过范围以验收记录为准。普通时间只用于值传输、排序和分组；经用户确认，比较/过滤谓词归 C3a，不据此启用时间分析。微秒作为本阶段最高小数秒精度；更高精度、带时区逻辑声明及非 UTC instant 解释归 C3a。

| 后端 / 本阶段物理形态 | C2 正向目标 | 限制与相邻负向项 |
| --- | --- | --- |
| PostgreSQL 普通表 | 原生 boolean；timestamp without time zone，精度 0–6；text、varchar(n)、char(n) 的映射核验；既有 signed/float/date/Decimal 回归 | timestamp 保留 civil 字段，不附加主机时区；字符定长填充与比较必须与逻辑 string 一致才可用于键/分组，否则该形态保持结构化拒绝并记差距；不新增 UInt、timestamptz、高精度和 infinity 支持 |
| MySQL InnoDB | signed 整数保持；TINYINT/SMALLINT/MEDIUMINT/INT/BIGINT UNSIGNED 分别映射 uint8/uint16/uint32/uint32/uint64；BOOL/BOOLEAN 的显式语义绑定；VARCHAR/TEXT 家族；DATETIME(0–6) 与 TIMESTAMP(0–6) 的精确传输 | 字符继续限定 utf8mb4_0900_bin；CHAR 单独验证尾空格，不直接归入成功集合；BIT/ENUM/SET/JSON 排除；TIMESTAMP 只验收显式 UTC 会话下的 UTC 值约定，非 UTC/无法证明会话事实保持拒绝；不得取服务器默认或主机 TZ 猜测 |
| SQLite main 普通表 | INTEGER/INT/BIGINT 既有 int64；增加 TINYINT/SMALLINT/MEDIUMINT/INT2/INT8 声明别名，逻辑仍为 int64；REAL/DOUBLE/DOUBLE PRECISION/FLOAT → float64；TEXT/VARCHAR(n)/CHAR(n)/CLOB → string；BOOL/BOOLEAN 与 DATETIME/TIMESTAMP 的受限约定存储 | 仅下节 owner 契约确认并贯通上游后启用 Boolean/timestamp；字符必须 BINARY 且实际 text；不把声明宽度当实际约束；不新增 UInt、Decimal、BLOB、epoch 数字时间、混合时区文本 |
| Trino 当前 Iceberg BASE TABLE | boolean；varchar/varchar(n)；timestamp(0–6) without time zone；既有数值/date 回归 | CHAR 尾空格单独负向/差距核验；不新增原生 UInt 或带时区/更高精度 timestamp；C2 不解除 connector 检查，C5 负责通用化 |
| ClickHouse 当前本地 MergeTree | UInt8/16/32/64；Bool；String、LowCardinality(String)、Nullable(String)、LowCardinality(Nullable(String))；这些标量合法的 Nullable 包装；DateTime 与 DateTime('UTC') | plain DateTime 仅在实际引擎事实为 UTC 时验收；不抹除显式非 UTC 时区；DateTime64、Date32、FixedString、Int128/256、UInt128/256、复杂嵌套及非法包装排除；Bool 与 UInt8 保留不同逻辑类型 |
| DuckDB 普通表及既有输入 | 对应 Boolean、各 UInt 宽度、string、普通 timestamp 的共同旅程；保留此前其他类型/方法 | 不将远程子集限制倒灌至 DuckDB；文件、JSON、复杂类型、私有状态、临时资源及 retained import 继续回归 |

字符的“核验”必须落成明确的允许/拒绝结果：除普通变长字符串外，不能仅依靠 mapper 返回 string 放行。包含尾空格、空串、大小写与非 ASCII 的比较、分组和排序结果必须与已有 string owner 一致；无法保持时保留拒绝，不通过 trim、大小写转换或改 collation 修复数据。

### 2.1 Boolean 与 SQLite 存储的 owner 决策

先对齐 [datasource](../../specs/semantic/datasource-layer.md)、[semantic 对象模型](../../specs/semantic/semantic-object-model.md) 和 [Analysis](../../specs/analysis/python-analysis-design.md)：source_column 的 dtype 是断言，不是任意强制转换。拟在 owner 文档中明确下列有限物理表示；实施第一步验证 live Help、load/readiness 与映射路径能否采用同一契约，不从 Analysis 绕过上游。

- MySQL 的 BOOL/BOOLEAN 经数据库元数据可能已成为 TINYINT(1)，不能从这个名称推断最初 DDL 的语义。只有显式 boolean 声明与兼容物理整数表示、实际值域验证共同成立才按 Boolean 消费；普通整数声明保持整数，不自动把非零值转 true。兼容范围锁定 TINYINT(1)，更宽整数和 BIT 不参与这项适配。必要列全域仅允许 0、1、NULL。
- SQLite BOOL/BOOLEAN 声明结合显式 boolean 语义声明，实际 storage class 仅 integer/null 且值为 0/1/NULL；TEXT 'true'、REAL 非整数、整数 2/-1 均拒绝。其他 INTEGER 列不自动变成 Boolean。
- SQLite DATETIME/TIMESTAMP 拟采用固定宽度 civil 文本 `YYYY-MM-DD HH:MM:SS.ffffff`，0001–9999 年、合法公历日期与时间、无 offset/Z，实际 storage class 仅 text/null。固定格式保障字典序与时间顺序一致；不接受可变精度、无小数秒、epoch 数值、非法日期或混合表示。精确解析与回写核对，不通过 SQLite 的宽松日期函数归一化损坏值。
- 若上游当前无法保留这些断言并读取同一物理事实，先实现最小内部 owner 适配和文档同步；不新增公共 authoring 参数。无法在现有公共契约内完成则停在该单元的设计阻塞，不声称 SQLite Boolean/timestamp 已完成。

## 3. 方法、数值与传输契约

| 类型 | 必验基础用途 | 不由本阶段自动获得的用途 |
| --- | --- | --- |
| Boolean | Population 投影、where 的 true/false/NULL、维度分组、相等比较、稳定排序、count；身份验证沿既有非空/唯一规则 | Boolean sum/mean 等数值解释；通过数值 cast 创造新 Measure |
| UInt | 投影、where、维度、主键/关系键、相等与范围比较、rank/limit、直接列 count/min/max/sum；小值输入的既有 mean/weighted mean/ratio 回归 | 任意 UInt64 算术自动精确；混合 signed/unsigned 隐式窄化；新表达式能力 |
| string / 合法包装 | 投影、过滤、分组、键、count、已有合法 min/max、排序/limit；空串、尾空格、Unicode、NULL | 改写 collation、trim 或 FixedString 解码 |
| 普通 timestamp | 投影、维度分组、排序/limit、count；仅现有语义允许的直接列 min/max | temporal axis、bucket、snapshot/validity 新范围；timestamp sum/mean |

UInt64 必须全值域保真，特别是身份值 `2**63` 与 `2**64-1`，不能经 float、signed int64、JSON 浮点数或字符串排序中转。传输的 Arrow uint64、Parquet logical unsigned 和冷读 dtype 都要核对；关系匹配和重复身份检测必须比较原值。

SUM 按现有公开结果类型契约执行（当前 UInt 的 SUM 为 int64，min/max 保持 UInt）：源可能有更宽累加器，但不能因此扩大公共结果类型或默默截断。可表示结果精确返回，超出结果类型范围结构化失败，且在发布前发生。ClickHouse unsigned 原生累加可能回绕，必须在下推前选择可证明精确的宽累加/校验路径，最终检查结果范围；不能在已回绕结果上再做范围验证。MySQL Decimal integer SUM 保持整数解码。既有公开浮点 mean/ratio 不承诺 UInt64 极值的精确有理数结果；高值测试不能用浮点 oracle 掩盖误差。

保留逐批传输与关闭生命周期，包含顶层 scalar、嵌套 typed identity、relationship keys、components/parts。不可为了转换而收集完整源表到本地；新增源存储验证可用只读聚合计数，必要列全域检查须早于结果过滤，即使最终输出为空也不能跳过。失败不发布 Artifact、不残留部分 Parquet 或可复用的坏缓存。

## 4. 实现顺序与现有拥有者

1. 固定 owner 契约与证据：记录 C1 工作区差异；读取 live `marivo.help("datasource.source_column")`、`marivo.help("analysis.actions.execute")` 及其实际可达目标；验证 SQLite/MySQL 显式绑定、timestamp 单位和字符比较边界。新增测试前读取 `marivo-test-fixtures` skill。先给负向/边界案例，再修改准入。
2. 物理映射与必要列校验：`marivo/datasource/engines/{mysql,sqlite,postgres,trino,clickhouse}.py` 的既有 connect/inspection 路径与 `marivo/analysis/materialization/*_execution.py::get_schema` 保持一致；仅实际存在映射缺口时修改 datasource owner。复用 `SourceColumnDependency`/`EntitySourceDependency` 和 `source_type_errors`，不新增全局类型注册表、adapter 私有依赖集合或公共兼容别名。
3. 方法准入和精确执行：`operators/{scalar,mysql,sqlite,postgres,trino,clickhouse}_support.py::supported_type/unsupported_reason`（shared 对应 `supports_scalar_type`）分别锁定类型用途；`mysql_execution.py::_lower`、`clickhouse_execution.py::_lower` 处理必要的整数聚合安全性。不能简单给 shared whitelist 加类型后让所有后端/方法一并激活。
4. 传输与发布：`scalar_sql_execution.py::_cell`、`ScalarBatchStream`、`postgres_execution.py::_transport_row` 和具体 cursor 流检查 UInt/Boolean/timestamp；如涉及 shared Runtime，最小修改 `admission.py::_validate_source_schema`，不改 C1 的依赖事实。复用 `SourceSchemaError` 的 missing/unsupported/mismatch 区分；非法存储/结果溢出沿用 `MaterializationError`，expected/received/repair 明确类型、关系列和恢复动作，不输出业务值或凭据。保留原始 driver 异常。
5. 按后端串行真实旅程及冷读，修复后执行全量门禁；同步披露并写 `2026-09-16-multisource-capability-c2-acceptance.md`。实现中任何文件范围扩展先说明必要性；不清理前序修改。

新测试集中于拟新增 `tests/lazy_scalar_type_fixtures.py`、`tests/test_lazy_scalar_types.py`、`tests/test_lazy_scalar_type_runtime.py`，不建立第二套 Runtime 或 registry。复用 `lazy_execution_fixtures.make_execution_registry`、`lazy_scalar_source_fixtures.registry_for`、`lazy_postgres_fixtures.registry_for` 和 `lazy_source_dependency_fixtures.py` 的独立 SQL 捕获；先检查 conftest/shared_fixtures 中可复用 fixture。各后端管理员 helper 仅建立/清理 UUID 专用表，Dataset 使用 reader；DuckDB/SQLite 置于 pytest 临时目录。

## 5. 独立验收数据与负向矩阵

- UInt 小值集 `0,1,2,255,NULL`：非 NULL count=4、sum=258、min=0、max=255；各宽度另用最大值。UInt64 边界集 `0,2**53+1,2**63,2**64-1,NULL` 验证精确投影、排序、过滤和键；sum 单独以 `[2**63-1]` 验证成功，以 `[2**63-1,1]` 与 `[2**64-1,1]` 验证超出 int64 的失败，不能把这个集合当正常聚合成功案例。其他结果类型按现有 owner 计算范围。
- Boolean 集 `false,true,NULL,true`：非 NULL count=3，true=2、false=1、NULL=1；分组及筛选预期直接写常量。损坏 2/-1/'true' 分别验证存储拒绝，普通 TINYINT 的 2 必须仍是合法整数。
- 字符集 `'', 'A', 'a', 'a ', '中', NULL`：逐值与 NULL 保真，按 owner 的二进制排序计算预期；定长 CHAR/FixedString 不借 trim 变为同值。LowCardinality 与普通 String 对相同输入给出相同结果。
- timestamp 用 `2024-02-29 00:00:00.000000`、`2024-02-29 00:00:00.000001`、`2024-03-01 12:34:56.123456`、NULL；精度 0 类型用整秒组，不先插入无法表示的微秒。比较/范围与顺序由 Python datetime 常量给出，timestamp 不经 float epoch。SQLite 追加非法闰日、24 点、offset、可变长度、数字存储；PostgreSQL 追加 infinity；MySQL 追加 zero/非法时间。ClickHouse DateTime 另按实际秒精度与原生范围提供边界，不宣称全 Gregorian 范围。
- 每类至少一条 load→Dataset→source→Arrow→Parquet→新进程零源访问冷读旅程；源聚合、parts、retained rollup 用独立整数/Decimal 方程对照。冷读不重新解析时区，改变主机 TZ 不改变已持久化值。
- UInt64 高值身份同源关系、复合身份、重复键、缺失目标、fanout；必要类型损坏即使 limit=0/where 输出为空也失败；未使用损坏/复杂列继续遵循 C1 裁剪。验证源 SQL 只投影必要列以及合法小结果的传输行数。
- 负向邻接：timestamp 时间轴/hour bucket、带时区/超精度声明、Decimal 新组合、计算型 measure、Linear、跨源 join、remote retained upload、sampling/Event/Lifecycle、ClickHouse Distributed 与 Trino 非 Iceberg 在 C5 前保持现状。DuckDB 既有正向方法不能被这些远程负向断言覆盖。

所有类型期望写为独立常量/标准库整数、Decimal、datetime，不复用生产 mapper/lowering 作为 oracle；SQL driver 捕获与 Runtime 结果分开记账。完整语句角色/计数对账仍归 C3a/C10。

## 6. 验证命令与环境顺序

以下为计划命令；实际执行和结果见验收记录。从仓库根执行，先定向再广门禁；新增模块名即上节约定名称。opt-in 缺服务或 skip 不算成功。

```bash
make test TESTS='tests/test_lazy_scalar_types.py tests/test_lazy_source_schema.py tests/test_lazy_scalar_admission.py tests/test_lazy_postgres_admission.py tests/test_lazy_scalar_execution_adapter.py tests/test_lazy_duckdb_execution_adapter.py tests/test_lazy_backend_dispatch.py tests/test_lazy_scalar_transport.py tests/test_lazy_clickhouse_transport.py tests/test_lazy_trino_transport.py tests/test_lazy_postgres_errors.py'
make test TESTS='tests/test_datasource_metadata.py tests/test_datasource_projected_inspection.py tests/test_datasource_authoring_inspection.py tests/test_lazy_source_dependencies.py'
make runtime-test TESTS='tests/test_lazy_scalar_type_runtime.py tests/test_lazy_sqlite_methods.py tests/test_lazy_sqlite_runtime.py tests/test_lazy_materialization_execution.py tests/test_lazy_retained_runtime.py tests/test_analysis_decimal_e2e.py' RUNTIME_WORKERS=1
MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_scalar_type_runtime.py tests/test_lazy_source_dependency_runtime.py tests/test_lazy_postgres_methods.py tests/test_lazy_postgres_runtime.py' RUNTIME_WORKERS=1
MARIVO_MYSQL_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_scalar_type_runtime.py tests/test_lazy_source_dependency_runtime.py tests/test_lazy_mysql_methods.py tests/test_lazy_mysql_runtime.py' RUNTIME_WORKERS=1
MARIVO_CLICKHOUSE_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_scalar_type_runtime.py tests/test_lazy_source_dependency_runtime.py tests/test_lazy_clickhouse_methods.py tests/test_lazy_clickhouse_runtime.py' RUNTIME_WORKERS=1
MARIVO_TRINO_ANALYSIS_TEST=1 make runtime-test TESTS='tests/test_lazy_scalar_type_runtime.py tests/test_lazy_source_dependency_runtime.py tests/test_lazy_trino_methods.py tests/test_lazy_trino_runtime.py' RUNTIME_WORKERS=1
make test TESTS='tests/test_lazy_temporal_source.py tests/test_lazy_population_sampling.py tests/test_lazy_retained_fold_matrix.py tests/test_analysis_help.py tests/test_analysis_help_resolution.py tests/test_lazy_disclosure.py tests/test_lazy_disclosure_examples.py tests/test_analysis_disclosure_journeys.py'
make runtime-test TESTS='tests/test_lazy_temporal_runtime.py tests/test_lazy_event_runtime.py tests/test_lazy_lifecycle_runtime.py tests/test_lazy_candidate_runtime.py tests/test_lazy_driver_runtime.py tests/test_lazy_distinct_runtime.py tests/test_lazy_distribution_runtime.py tests/test_lazy_adapter_runtime_acceptance.py' RUNTIME_WORKERS=1
make typecheck TYPECHECK_TARGETS='marivo/analysis/operators marivo/analysis/materialization marivo/datasource/engines tests/lazy_scalar_type_fixtures.py tests/test_lazy_scalar_types.py tests/test_lazy_scalar_type_runtime.py'
make lint-agent LINT_TARGETS='marivo/analysis/operators marivo/analysis/materialization marivo/datasource/engines tests/lazy_scalar_type_fixtures.py tests/test_lazy_scalar_types.py tests/test_lazy_scalar_type_runtime.py'
make check-agent
npm --prefix site run verify:content
npm --prefix site run build
git diff --check
```

类型检查和 lint 参数已覆盖拟修改模块；实施若修改其他 Python 文件，按实际 diff 补入相应完整路径并在验收记录保存最终命令。Help 漂移/可达性/预算及公共导出快照由定向组和 `check-agent` 覆盖，不能只检查文档文字。

开始真实验收前运行 `bash tests/multisource_environment/manage.sh status postgres-analysis`，记录专用项目状态与服务归属。先 DuckDB/SQLite，再 PostgreSQL/MySQL/ClickHouse，最后 Trino；使用现有 runbook 的 `start clickhouse` / `start trino` 串行切换，不并行重型门禁。实施时仅操作专用 `marivo-multisource` 项目，结束恢复开始状态；不启动 MinIO、不运行 release-check、不重建/删除 volume。计划阶段不启动服务。管理员 fixture 和 reader 执行分别记录，日志不含凭据。

## 7. 披露与验收记录

同一变化同步 `docs/specs/analysis/python-analysis-design.md`、涉及物理表示约定的 semantic/datasource owner 文档、`marivo/analysis/datasets/_disclosure.py` 的 native Help/动态 guidance、必要的 datasource Help 拥有者及中英文 `site/src/content/docs/{docs,zh-cn}/latest/concepts/analysis-workflow.mdx`。具体 Help 事实先定位原 owner，不能增设 renderer 清单或为本阶段添加公共 API。核对 CLI 仍路由唯一 Help coordinator，示例必须可运行；没有行为变化的 CLI 不做无关修改。

packaged skills 仅检查是否出现陈旧边界，本计划不授权修改；若确需编辑，列出具体陈旧内容后按仓库要求取得用户明确批准。C0 保持历史基线，不改写其观察结果；总计划仅在真实验收通过后标注 C2 状态。

验收文件按后端×物理类型×用途列出通过、拒绝和阻塞，附最终命令/退出状态、reader/表形态、Arrow/Parquet dtype、独立预期、冷进程零源访问、失败无发布和环境恢复证据。CHAR 等核验项必须写明最终结论；SQLite owner 决策不能以“后续再定”冒充交付。C2 不声称 C3a 时间语义、C5 新表形态或 C10 安装包验收完成。

## 8. 实施确认的细化

- 用户确认 timestamp 时间谓词归 C3a；C2 保留传输、分组、排序。
- Trino/Iceberg 将 timestamp(0/3) DDL 暴露为 timestamp(6)，错误的精度断言仍拒绝，按实际 timestamp(6) 执行；CHAR DDL 同样以实际暴露的 varchar 验收，不宣称原生 CHAR 准入。
- Arrow timestamp(s) 在写 Parquet 前无损拓宽为 timestamp(ms)，receipt 记录实际毫秒存储；逻辑秒精度与值不变。
- 为使公共 load/inspect/preview/source_health 与 Analysis 一致，增加 datasource cursor Boolean 原始值检查、metadata 精度解析与必要的 source_column Help 约束；不新增公共参数。
- 实施补充 observation/contracts.py、datasets/descriptors.py 的精确 timestamp 类型标识，以及 UInt 谓词字面量类型/范围检查；这是既有操作支持新标量的必需路径。
- limit(0) 不属于既有公开合同；必要列损坏的空输出负向使用无匹配 where 验证。
