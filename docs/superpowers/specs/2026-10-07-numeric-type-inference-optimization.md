# 聚合与组合运算的数值类型推导优化

Date: 2026-10-07

Status: N1–N4 implemented for ordinary Metric aggregation/composition and retained continuation; bounded validation is recorded in section 11. N5 remains deferred.

Decision update: prefer standard Ibis expressions for supported computations; accept documented numerical precision loss and differences from SQL-standard or earlier Marivo precision rules. This supersedes the initial proposal's default of preserving exact custom finishing.

Research baseline: `panda@c73356872c5e8ac286d8a33f35a8e3e8ef2266b8` plus the working tree inspected on 2026-10-07. The worktree contains concurrent uncommitted changes; the SHA is a reference point, not a claim that all inspected files match that commit.

## 1. 结论与交付边界

采用用户明确选择的方向：Ibis 已支持的计算优先直接使用 Ibis，接受其后端运算及结果
转换带来的部分精度损失，也接受与 SQL 规范或现有 Marivo 精度规则不一致的情况。
SQL 规范和引擎调研用于理解行为，不作为另造一套统一精度算法的理由。

优先解除 Decimal/int、float/int 和 Ibis 可接受的 Decimal/float 组合限制；sum、mean、
percentile、ratio、weighted mean 都先尝试对应的标准 Ibis 表达式。结果可以沿用 Ibis
执行后的类型，不强制旧的 Decimal(38,s)、HALF_EVEN 或先精确运算再一次 float finish。
这包含已有同型输入的行为调整，不应被描述为纯内部等价重构。

减少转换的原则是：保留原字段，接受 Ibis 自己的必要提升；Marivo 仅在接口、持久化、
单位或明确业务口径需要时添加转换。无需用户为每一种普通数值损失另选 approximate
开关。不以“所有类型先转 float64”取代 Ibis 本身的推导。

本文前十节记录调研依据和获准设计；第 11 节记录实施结果及其验证范围。实施已同步以下
契约 owner；AGENTS.md 和 packaged skills 未修改：

- [Semantic object model](../../specs/semantic/semantic-object-model.md)：Metric 公式、单位、空值与精度意图。
- [Operators and frames](../../specs/analysis/operators-and-frames.md)：结果、保留状态、数值误差和继续聚合。
- [Python Analysis design](../../specs/analysis/python-analysis-design.md)：图绑定与精确物理实现选择。
- [Session state and runtime](../../specs/analysis/session-state-and-runtime.md)：执行、发布与恢复。
- [Runtime coverage](../../testing/runtime-coverage.md)：行为测试与执行路径的责任。

## 2. SQL 规范与常见引擎：可借鉴的规则及边界

### 2.1 SQL 规范证据

ISO 官方页面确认 SQL/Foundation 的公开版本为 ISO/IEC 9075-2:2023，并列出 2026
勘误；本次未取得该版完整规范正文，不把引擎公式或历史条款冒充为 SQL:2023 的逐条要求。
版本依据见 [ISO 官方目录](https://www.iso.org/standard/76584.html)。

可公开核对的 NIST FIPS PUB 127 所附 ANSI X3.135-1986，§5.8–5.9，印刷页 27–29
（PDF 页 37–39），明确区分 exact numeric 与 approximate numeric：MIN/MAX 保持输入
类型；exact SUM 保持 scale、precision 由实现决定；exact AVG 的 precision/scale 由
实现决定；exact 加减的 scale 为两侧最大值、乘法为两侧 scale 之和，除法精度由实现决定。
这是历史标准依据，不是对最新版本的完整符合性结论。
[原始标准公开文本](https://nvlpubs.nist.gov/nistpubs/Legacy/FIPS/fipspub127.pdf)

由此采用的设计原则是：区分精确与近似数值，按运算推导，不要求二元操作数声明类型相同。
不采用“SQL 规范规定所有引擎统一 Decimal(38,s)”这样的说法。Marivo 的整数 mean/ratio
返回 float64、Decimal 除法使用既定 finish scale，是当前自身方法契约；本提案允许后者
改为 Ibis 路线的实际结果与舍入行为，不以历史规则作为保留手写精确算法的前提。

### 2.2 引擎对照

下表是官方文档调研；只有第 3 节标明的 DuckDB/SQLite 表达式在本机实测。SQL Server
作为设计参考，不是新增 Marivo 后端。

| 引擎 | 可核对的行为 | 对 Marivo 的影响 |
| --- | --- | --- |
| PostgreSQL 18 | SUM(smallint/int) 返回 bigint，SUM(bigint) 返回 numeric；AVG(integer/numeric) 返回 numeric；MIN/MAX 可用于多种有序类型 | 接受输入、累加与结果类型不同；最终发布类型以完整 Ibis 执行和传输路线为准。[官方文档](https://www.postgresql.org/docs/18/functions-aggregate.html) |
| DuckDB | Decimal 加减乘维持定点算术，Decimal 除法返回浮点；Decimal 内部载体按宽度分档，宽载体可能更贵 | 接受 Ibis `/` 的浮点商和 `.mean()` 的实际输出；不为旧精度契约保留手写除法或全列 38 位转换。[数值类型](https://duckdb.org/docs/current/sql/data_types/numeric) |
| MySQL 8.4 | SUM/AVG 对 exact 输入返回 DECIMAL，对 approximate 输入返回 DOUBLE；exact `/` 的 scale 受 `div_precision_increment` 影响，默认增量 4 | 需要绑定影响计算的会话设置；最终 cast 无法撤销前一步除法舍入。[聚合](https://dev.mysql.com/doc/refman/8.4/en/aggregate-functions.html)、[算术](https://dev.mysql.com/doc/refman/8.4/en/arithmetic-functions.html) |
| Trino | Decimal 加减与乘法分别按最大 scale、scale 之和推导；不同 Decimal 的共同类型可能在 precision=38 附近装不下原值 | “先把两边转共同 Decimal”并非总是无损；ratio 尤其应保留独立分量。[官方规则](https://trino.io/docs/current/functions/decimal.html) |
| ClickHouse | Decimal 运算按内部宽度与 scale 推导；Decimal/float 不直接混算；Decimal 除法截断；文档明确 Decimal128/256 溢出检查存在缺口 | 接受已披露的舍入/截断，不把整数回绕当普通精度损失；是否可执行以 Ibis 适配与真实路线为准。[官方规则](https://clickhouse.com/docs/reference/data-types/decimal) |
| SQLite | SUM 的结果由实际输入决定，整数累加可溢出；AVG 返回浮点；没有可据此继承的固定 Decimal 运算保证 | 静态类型声明和 `CAST AS DECIMAL` 不足以建立 Decimal 路线。[官方聚合规则](https://sqlite.org/lang_aggfunc.html) |
| SQL Server | Decimal 加减 precision 为 `max(s1,s2)+max(p1-s1,p2-s2)+1`；乘法为 `p1+p2+1`；可能按 38 位上限缩减 scale；Decimal AVG 返回 `decimal(38,max(s,6))`，整数 AVG 仍返回整数族 | 证明规则存在引擎差异；不复制其公式，也不为对齐它添加自定义计算。[算术类型](https://learn.microsoft.com/en-us/sql/t-sql/data-types/precision-scale-and-length-transact-sql?view=sql-server-ver16)、[AVG](https://learn.microsoft.com/en-us/sql/t-sql/functions/avg-transact-sql?view=sql-server-ver17) |

引擎的 implicit cast 也不等于数学无损。DuckDB 区分普通隐式转换和比较、集合等场景的
combination casting，后者甚至可能接受 bool→integer。本方案不把这套较宽规则直接
移植到 Metric 的业务数值运算。[DuckDB 类型转换](https://duckdb.org/docs/current/sql/data_types/typecasting)

## 3. 本地探针与当前代码证据

### 3.1 表达式实测

环境：DuckDB `1.5.3`、Ibis `12.0.0`、PyArrow `25.0.1`、Python sqlite3 所用 SQLite
`3.53.1`。探针使用内存数据库与字面量，无业务源连接。以下是实际观察，不是 Marivo
端到端支持资格，也不是性能基准。

| 探针 | 实际结果 | 推论 |
| --- | --- | --- |
| DuckDB SUM(INTEGER/BIGINT)、AVG(INTEGER/BIGINT) | 分别为 HUGEINT、DOUBLE | 原生累加器可以宽于公开状态；无需为 SUM 先转换每一行 |
| DuckDB SUM、AVG、q25 over Decimal(12,2) `[1.01,1.02]` | 类型分别为 Decimal(38,2)、DOUBLE、Decimal(12,2)；q25=`1.01` | 原生 AVG/分位数不等于 Marivo Decimal mean 的 finish 规则 |
| DuckDB Decimal(12,2) × Decimal(8,3) | Decimal(18,5)，`1.25×2.125=2.65625` | 两侧可以异型，不必先统一 scale |
| DuckDB Decimal(12,2) × BIGINT | Decimal(31,2)，`1.25×3=3.75` | 原生混合精确乘法可复用，但仍需验证全域与溢出 |
| DuckDB weighted numerator / weight sum，value=`[1.01,1.02]`，weight=`[1,2]` | numerator Decimal(38,2)=`3.05`；denominator HUGEINT=`3`；直接商 DOUBLE=`1.0166666666666666` | 采用 Ibis 分开聚合后相除，可直接接受浮点商 |
| DuckDB `BIGINT(2^53+1) + DOUBLE(-2^53)` | `0.0`，而原输入精确和为 `1` | 这是允许披露并接受的 native 精度边界，不为恢复这一位自动改走手写精确算术 |
| DuckDB `SUM(BIGINT_MAX * 2)` | 乘法先溢出；把一个乘法操作数转 HUGEINT 后得到 `18446744073709551614` | 只拓宽 SUM 不保护前面的乘积；这不是允许发布超 int64 状态 |
| DuckDB Decimal(38,0) 最大值加 Decimal(2,1) `0.1` | 共同 Decimal(38,1) 转换失败 | 共同类型可能缩窄整数部分；不能删除这个检查 |
| DuckDB DATE `[2026-01-01,2026-01-02]` 的连续中位数 | TIMESTAMP `2026-01-01 12:00:00` | 非数值有序聚合需要正确输出族，不能硬塞 NumericRelation |
| DuckDB INTERVAL 的 `quantile_cont` | 本机签名不接受 | Duration 分位数不是删除 Marivo 白名单就能完成 |
| SQLite SUM/AVG over `[1,2]` | integer=`3`、real=`1.5` | 检查完整 Ibis 表达式路线，接受其结果类型 |
| SQLite `5/2`、`5.0/2`、`CAST(1.01 AS DECIMAL(12,2))` | `2`、`2.5`、real=`1.01` | 使用 Ibis 的 `/` 语义与编译结果，不直接拼接 SQL 或伪称原生 Decimal 精确支持 |

Ibis 与引擎之间另有独立差异：

| Ibis 表达式 | Ibis 推导 | DuckDB 原生结果/构造情况 |
| --- | --- | --- |
| Decimal(12,2).mean() | Decimal(12,2) | DOUBLE |
| Decimal(12,2) × 同类型 | Decimal(12,2) | Decimal(18,4) |
| Decimal(12,2) × int64 | Decimal(12,2) | Decimal(31,2) |
| Decimal(12,2) × Decimal(8,3) | 构造时 IbisTypeError | 原生 SQL 可执行，返回 Decimal(18,5) |

因此必须分别核对声明类型、Ibis 表达式类型和后端输出类型。不能仅凭 `.type()` 发布
精度事实；也不能仅凭 native SQL 成功就新增 Analysis 实现资格。

补充执行探针（同一版本环境）：Ibis `.execute()` 和 `.to_pyarrow()` 还可能在 SQL
返回后做结果转换。Decimal(12,2) 的 `[1.01,1.02]` 调用 `.mean()`，`.execute()` 返回
`Decimal('1.02')`，而 `.as_table().to_pyarrow()` 返回 `Decimal('1.01')`，类型为
`decimal128(12,2)`；原生 SQL AVG 则是 DOUBLE。两种 Ibis 结果出口也不保证相同舍入。
Decimal/int weighted mean 返回 float `1.0166666666666666`；Decimal/float weighted
mean（权重 `[0.5,1.5]`）可直接构造并执行，返回 float `1.0175`。
本方案接受这些完整 Ibis 路线的行为，不要求绕过 Ibis converter 来保留更多小数。
实施应沿 Marivo 所选 provider 的正式批传输路径绑定结果，不混用 `.execute()` 与
Arrow 导出的值建立同一个结果契约。上述探针不代表现有 provider 已采用任一出口。

### 3.2 当前限制的责任位置

| 位置 | 已核对的限制/机制 | 所需调整 |
| --- | --- | --- |
| [graph_preflight.py](../../../marivo/analysis/materialization/graph_preflight.py)，`EntitySchema.field_type` | 精确匹配 int64/float64；保留 Decimal/Duration 元数据 | 小整数/float32 的无损适配必须显式绑定原 schema，不能直接谎报 dtype |
| [graph_observation.py](../../../marivo/analysis/materialization/graph_observation.py)，weighted 分支 | weight 类型等于 value 类型；Decimal 使用 `2*s<=38`，Duration/int64 例外 | 保留两侧类型，让所选 Ibis 运算决定支持和结果，移除重复同型白名单 |
| [core/rules.py](../../../marivo/analysis/core/rules.py)，`ObserveWeightedMean` | 只有 `amount_type` 和 `weight_column`，没有独立 weight 类型 | 在现有参数中保留 weight 类型及可机械推导的状态事实 |
| [graph_lowering.py](../../../marivo/analysis/compiler/graph_lowering.py)，weighted lowering | weight 与 product 类型从 amount 推导，乘法前两边按相同类型转换 | 保留独立输入，只有必要时提升操作数，分别聚合分子分母 |
| [comparison.py](../../../marivo/analysis/methods/comparison.py)，`output_type` | 普通 ratio 要求同族；Decimal difference 要同 scale；Duration 要同单位 | 源端消费 Ibis 路线类型；固定消费者按已保存的数值行为执行，单位仍独立检查 |
| [builtin.py](../../../marivo/analysis/methods/builtin.py)，`specialize_numeric` | 多处同族特化；精确注册键仍控制真实支持 | 特化请求中的真实有序类型，不能伪装成同型键 |
| [decimal_precision.py](../../../marivo/semantic/decimal_precision.py) 与 [metric_graph_lowering.py](../../../marivo/semantic/metric_graph_lowering.py) | 已有 Decimal 加减乘规则，但其他消费者仍各自推导 | 缩减为必要的类型适配；已由 Ibis 接管的计算不再重复维护 precision 公式 |
| [numeric_sql.py](../../../marivo/analysis/compiler/numeric_sql.py) | Decimal finish 使用精确商余数与字符串系数转换 | 被标准 Ibis 表达式覆盖后移除对应自定义路径；确有独立调用者的部分单独评估 |
| [numeric_state.py](../../../marivo/analysis/methods/numeric_state.py) 与 [state_validation.py](../../../marivo/analysis/methods/state_validation.py) | 按状态字段类型 merge/validate，结果一次 finish | 按新路线保存状态类型及数值行为，撤销与新 native 行为冲突的旧 exact 断言 |

[standardize](../../../marivo/analysis/methods/references.py) 已有 I/F value 与 I/F weight
交叉组合，说明“所有 weight 方法都必须同型”并非统一语义事实。不过 reference weights
的非负及和为一规则与普通 weighted mean 不同，不能随着类型规则共享而合并业务语义。

## 4. 接受的数值差异与保留的业务边界

| 类别 | 目标决策 |
| --- | --- |
| 普通数值精度 | 接受 Ibis/后端引入的浮点舍入、Decimal scale 缩减、截断、聚合顺序差异，以及与 SQL 规范不同的返回类型 |
| 默认计算 | 不再要求所有 Decimal 运算保持 Decimal，也不要求所有结果 HALF_EVEN、一次舍入或精确有理数 finish |
| 业务公式 | sum/mean/ratio/weighted 的公式、量纲、贡献范围、配对和单位含义不因精度简化改变 |
| 空与未定义 | 保留 Null/Unknown/Undefined、空贡献、零权重和及零分母的业务策略；不得因原生除法输出 Inf 而丢弃 Cell 策略 |
| 溢出与无效值 | 后端明确异常应结构化返回；整数回绕、损坏载体、非有限结果不作为普通舍入接受；不为每种极端输入实现任意精度算法 |
| 保留状态 | mean 仍需 sum/count，weighted 仍需同一非空 pair 的 N/W，ratio 仍需原始分量；不能对投影均值或比例直接再平均 |
| 分位数意图 | 普通浮点舍入不等于抽样/sketch 近似；exact/approx 聚合身份继续区分，不自动以 approximate quantile 替代 exact 请求 |
| 身份与时间 | 不将 join key、distinct key 或 Entity identity 转成浮点；calendar month 不自动等于固定秒数 |

接受部分精度损失意味着允许第 3 节的大整数抵消结果，不能再用统一“必须精确到原整数”
断言将同一路线封禁。不承诺任意数据上的统一相对误差界；测试需记录典型及边界数据上的
实际差异。若特定后续方法确实需要比普通聚合更严格的精度事实，它在自己的边界判断，
不能因此强迫所有聚合都使用昂贵精确路线。

现有精确状态、误差传播和分母稳定性规则需按消费者逐项处理。普通 native ratio 不再仅因
不能满足旧自定义误差界而拒绝一个有限结果；仍检查其实际计算分母及既有零分母策略。
依赖误差上界的下游消费者不得继承已失效的证明，需重新验证或明确收紧其 continuation。
不借此全局删除 unrelated statistical、Duration、attribution 等方法的独立要求。

## 5. 目标类型与运算路线

### 5.1 Ibis 优先，而非新增通用数值推导引擎

沿现有 Semantic → Analysis → Ibis/compiler 路线处理四类事实：

1. 输入保留真实类型、单位与源 schema；构造标准 Ibis 表达式时让 Ibis 处理普通数值提升。
2. 方法层保留公式和业务语义，不重复实现所有后端的 precision/scale 规则。
3. 物理适配层核对 Ibis 表达式类型、编译后的后端行为及执行结果 schema。结果转换是
   完整 Ibis 路线的一部分；不把 SQL 类型或 `.type()` 任意一个单独当全部事实。
4. 持久化发布真实结果及状态类型，所需转换只在实际不兼容的边界加入。

类型绑定可以依赖后端与已绑定 schema，但不能扫描样本选择另一种公共结果类型，也不能
失败后自动切换算法。优先复用 Ibis/backend 的静态信息和现有元数据读取；仅对确有推导
缺口的表达式做受控 schema 探测，不新增每次执行的整源扫描。

本提案允许不同后端得到不同数值结果或 dtype。必须在同一执行、持久化和读取链上如实
记录该差异，避免将 DOUBLE 标为 exact Decimal，或把 Ibis 已经舍入的 Decimal 误说成
native SQL 原值。无需为了跨后端统一 dtype 对全部行进行重写。
每个物理实现只选定一个正式结果转换出口，按现有 provider 的批传输约定测试和保存；
不能逻辑 `.execute()` 采用一种 converter、物化时又临时采用另一种。Ibis 支持情况需
覆盖这一完整链条，单独的标量执行成功不是发布证据。

### 5.2 标准表达式优先顺序

表中为实现方向，不是对所有后端可执行性的预先承诺。

| 运算 | 首选 Ibis 实现 | 类型与精度处理 |
| --- | --- | --- |
| sum | `value.sum()` | 接受原生提升的累加/结果类型；仅在存储暂不支持该载体时做一次受检查适配 |
| mean | `value.mean()` | 接受完整 Ibis 路线的结果类型与舍入；不再默认展开成自定义精确 sum/count 除法 |
| min/max | `value.min()` / `value.max()` | 保留有序输入对应输出；非数值结果需正确结果族 |
| median/percentile | 对应 Ibis median/quantile 操作 | 核对后端是否保持请求的 exact/approx 身份，接受所选原生插值精度 |
| ratio | `numerator / safe_denominator` | 使用 Ibis 真除法语义及后端编译；支持 Ibis 可执行的异型数值对；零分母按业务策略处理 |
| weighted mean | 同一 pair mask 下的 `(value * weight).sum() / weight.sum()` | value/weight 保持独立类型；接受产品、累加和商的原生推导与舍入 |
| linear / difference | 标准加减乘表达式 | 只保留真实业务系数；±1 不人为写成 float 系数，允许 Ibis 的必要提升 |
| count / distinct | 对应 count/nunique 操作 | 保留完整身份与 distinct 定义，不为共享数值路径而转换键 |
| Duration | Ibis 支持并保留单位含义的运算优先 | 日历与固定时长不同；若需要 tick 适配，使用最小适配，不自动开发新插值算法 |

mean 直接执行 `.mean()` 与为了 rollup 同时保留 sum/count 并不冲突。不要为了减少一个
聚合状态而丢失继续聚合能力，也不要为了精确重现 sum/count 商而禁用 Ibis mean；两条
投影的数值差异按第 6 节处理。

### 5.3 混合类型接受策略

| 组合 | 推荐决策 |
| --- | --- |
| int/float | Ibis 支持时直接接受，不要求先证明 int64→float64 对全部输入精确 |
| Decimal/int | Ibis 支持时直接接受；商可以是 float，不强制为整数分量建立同 scale Decimal |
| Decimal/float | Ibis/后端完整路线可执行时接受，可返回 float；取消先前提案的全局拒绝与新增显式 approximate 开关要求 |
| 不同 Decimal precision/scale | 先直接构造；Ibis 拒绝时选择最小公共类型适配，接受明确的 scale/舍入变化；不能假装任意两个 Decimal 都有无损共同类型 |
| int8/16/32、float32 | 尽量保留原输入；只有现有 graph/Arrow 接口需要时适配一次，不先统一全部源列 |
| uint64、int128、更宽 Decimal | 引擎支持与 Marivo 能存储是两件事；优先实现简单的受检查边界适配，无法表示时明确拒绝；不回绕到 int64 |
| 不同固定 Duration 单位 | 按可用 Ibis 操作做必要单位适配；仍不能将 calendar interval 静默转换为固定 tick |

不以 `p1+p2>38` 等手写公式抢先拒绝所有 Ibis 可执行组合。现有 Decimal 公式可暂留给
仍依赖它的未迁移消费者，但已切换的普通聚合/组合不再有平行 precision owner。
对于后端已知可能整数回绕的运算，使用现有范围检查或必要提升；无法可靠承载时限制该
物理路线，不扩展成通用任意精度计算系统。

### 5.4 Ibis 不支持时的处理

按以下顺序选择，不同时维护等价能力的多套默认算法：

1. 标准 Ibis 操作，无额外转换。
2. 最小输入或输出适配后仍使用标准 Ibis 操作；不为复刻旧精度而连续 cast。
3. 能力仍缺失则准确报告该后端/类型组合不可用。只有重要且确实缺失的能力才另外评估
   私有 Ibis 扩展或上游改进；不因 native SQL 能写就立即新增私有算子和手写 SQL。

本机 Decimal(12,2)×Decimal(8,3) 就属于第二步候选。把前者适配到 scale=3 会增加产品
scale，这可以是接受的实现折中，不需要仅为恢复 scale=5 新写乘法节点。但要核对适配
范围与实际结果，不能把 precision=38 附近的整数部分溢出藏起来。

## 6. 源端、固定结果与恢复的一致性

本次简化不能把“源端接受 native 精度”与“固定续算仍宣称原有 exact 契约”同时保留。
需同步更新所涉方法的类型、状态和数值身份，沿现有 method/implementation contract
记录足够的结果类型与执行行为；不另建全局 precision registry 或公共精度模式开关。

- **原结果读取**：保存 Ibis 实际生成的主值与 dtype，冷读保持保存值；不能重新计算一遍
  改成更精确的值。按当前 Store 信任与恢复契约读取，不新增重复内容审计。
- **继续聚合**：仍从原始 N/W、sum/count 等充分状态计算，不从已舍入主值倒推。固定
  Artifact 继续使用当前获准的本地消费者；不因 Ibis 优先就重新打开源库或把固定结果
  灌入 DuckDB。相应本地消费者改用捕获的输出类型、舍入和 null 策略。
- **数值一致性**：相同保留结果的恢复要求值和 schema 一致；直接重算与不同分区的
  rollup 可以有原生聚合/转换导致的数值差异，不再普遍要求 bitwise equality。类型和
  业务 Cell 策略仍需对应；各方法用实际研究过的舍入精度/容差判定，不随测试失败扩大。
- **可继续的范围**：如果某个原生算法没有可合并状态，不伪造 rollup 能力。例如只有
  quantile 投影值时不能恢复总体分位数；需要原分布的继续计算仍明确不可用。
- **下游保证**：若 attribution、reference 或统计方法依赖 exact 分量或已认证误差，
  将失效保证从其输入契约中移除并做受影响验证；不能把 native 数值结果标成 exact。

例如源端 Decimal mean 可能由 Ibis converter 舍入到两位；固定消费者不能无说明地
输出旧的六位 Decimal。应绑定新结果的两位类型和相应输出转换，并验证 ties、负值和
空输入。同一条链内的这类一致性工作属于 Ibis 简化的必要交付；不要求复刻每个后端的
全部内部累加顺序或为跨后端完全一致重建 SQL 数值系统。

## 7. 减少转换与自定义计算的具体位置

| 当前成本/限制 | 优化方向 |
| --- | --- |
| `numeric_sql.py` 的精确整数/Decimal 商余数、字符串系数提取和拼装 | 用标准 Ibis `/` 替代已覆盖的普通 ratio/mean/weighted finish；同步更新结果类型和调用者，移除不再使用的代码 |
| weighted mean 的同型输入与预先全列 Decimal 转换 | 独立绑定 value/weight，保留同一 pair mask，直接用 Ibis 产品与聚合 |
| Semantic、Analysis 与固定消费者重复 precision 公式 | 源端以 Ibis 路线结果为主，固定消费者只保留重现持久化契约所需的规则 |
| 为强制 D(38,s) 或统一 float64 增加转换 | 默认删除；确有输出接口或存储承载要求时只在边界处理 |
| 为旧 exact/误差目标增加的每次运行验证 | 在受影响 native 方法中调整或删除；不能把已经放弃的精度目标变成新准入障碍 |
| 重复 cast | 合并无用途的转换；用户 authored cast、业务单位转换、零分母和 null 处理仍有实际含义 |

`SUM(cast(x,T))` 与 `cast(SUM(x),T)` 不自动等价。前者可改变乘积/累加范围和每行舍入，
后者无法修复已发生的溢出。转换优化需核对真实执行计划，不以 SQL `CAST` 数量作为唯一
指标；引擎可能自己插入或消除转换。

percentile 继续优先使用 Ibis 原生操作。Duration 等不支持的签名先考虑必要类型适配，
仍不可用则记录缺口；本轮不新增手写排序/插值，也不 fetch contributions 后 Python
排序。date/timestamp 极值和分位数需正确的非数值结果族，不能通过强转 float 绕过。
q=0/1 仍是独立低优先级 API 决策；本次精度授权不改变 exact/approx 算法选择。
[DuckDB 分位数定义](https://duckdb.org/docs/current/sql/functions/aggregates#quantile_contx-pos)

## 8. 实施分批与完成条件

| 阶段 | 内容与主要 owner | 完成条件 |
| --- | --- | --- |
| N1 Ibis 行为与契约切换 | 为现有方法建立 Ibis 表达式、实际 dtype、数值差异及状态对照；修改受影响精度契约与类型绑定 | 清楚列出哪些同型输入也会改变值/dtype；不把本阶段称为等价重构 |
| N2 原生聚合与组合 | sum/mean/ratio/weighted/linear 用标准 Ibis；开放可执行的异型数值组合；移除对应手写 finish | DuckDB table/Parquet 的本批公开路径、空值/零分母和实际 schema 通过；没有双算法默认或失败回退 |
| N3 固定续算与下游 | 调整 typed state、固定消费者、cold recovery 及受影响 attribution/reference/统计契约 | 原值读取不漂移；继续计算的类型和数值容差明确；不会继承失效 exact 证明；与 N2 同批发布 |
| N4 转换与后端扩展 | 清理重复 cast 和未使用 helper，补小类型适配；逐个后端开放经过验证的 Ibis 组合 | 区分文档、编译、真实 Runtime 证据；记录转换/自定义代码减少与性能 |
| N5 独立缺口 | Duration 单位/分位数、有序非数值结果、更宽持久化载体 | 重要且 Ibis 仍无法直接完成的能力单独设计，不阻塞已支持计算的简化 |

N2 与 N3 是同一次行为变更的生产闭环，不得先发布新 native 值却保留旧 fixed/下游契约。
每批明确方法和执行路径，优先真实连接的消费者；不展开无关后端/存储笛卡尔矩阵。
远程后端的本轮实际执行范围见第 11 节。SQLite 可采用已验证的 Ibis 浮点结果，
但不能把 `CAST AS DECIMAL` 声称为精确 Decimal 支持；Ibis 不支持的 Decimal 输入仍拒绝。

版本绑定实施时的当前协议与 method/implementation contract。已有 Artifact 不在读取时
静默改变算法；不兼容状态按当前恢复契约处理，不新增双读、源重放或旧状态猜测。
不为保留旧精度默认增加一个长期 exact/native 双路线。

实施同批更新实现、Help、动态 continuation、独立可达性/预算测试、示例和英中文档。
packaged skills 仅在确有工作流变化并取得仓库要求的明确授权后修改；本轮无需修改。

## 9. 验收与性能证据

按行为与独立风险组织测试；新的验收目标是“业务公式正确、与指定 Ibis 路线一致、类型
和持久化真实”，不再是“所有运算匹配手写任意精度结果”。

| 责任 | 需要的独立证据 |
| --- | --- |
| 路线一致性 | 直接 Ibis reference 与 Marivo lowering 的 source 值/dtype 对照；捕获 Ibis converter 行为，避免两端都只检查错误的 `.type()` |
| 数值差异 | 用 Fraction/Decimal 原值 oracle 量化舍入/抵消差异；oracle 用于说明损失，不把每个非零误差判为失败 |
| 混合输入 | Decimal/int、float/int、可执行 Decimal/float、不同 Decimal scale；确认不再仅因旧同型检查拒绝 |
| 业务公式 | 独立验证 weighted pair mask、分母口径、单位、空组/全空/零和、Null/Unknown/Undefined；Ibis 可执行不代表业务正确 |
| 中间范围 | 乘法在 SUM 前溢出、后端异常、有限输出；禁止回绕或用失败后的 float 重试掩盖错误 |
| 状态/恢复 | 实际 Arrow/Parquet dtype、读取原值不漂移、fixed rollup/cold recovery；业务状态不变，计算值按新方法的容差检查 |
| 下游 | 原 exact/误差事实失效后，受影响 consumer 的契约及数值判断仍正确；不把近似量伪装成 exact 分量 |
| 披露 | 描述真实 dtype/精度边界；错误区分业务不合法、Ibis 构造失败、后端不支持及输出无法承载 |
| 简化效果 | 原生 Ibis、旧实现、新实现的表达式/计划和耗时对比；记录删除的 custom 算法与转换，不只比较 SQL 长度 |

普通浮点和 Decimal 转换采用方法/后端可解释的容差，不新增所有聚合逐次计算严格误差
证明的框架。极端抵消可以保留为已接受的 native 行为用例；现有更严格消费者的验收
独立保留。若业务公式、pairing 或零分母状态变化，则不是允许的普通精度差异。

性能报告分开比较两种情况：新旧数值策略变化带来的简化收益，以及新策略内部删除
冗余转换的等价优化。记录输入、分组、dtype、版本、warm/cold、重复运行中位数、峰值
资源和实测精度差异；不能把放宽精度后得到的收益写成“完全等价”。

日常先跑类型/公式测试和受影响 `make runtime-test TESTS=...`，共享行为变更用
`make check-agent`。实施后的具体检查与边界见第 11 节。

## 10. 可复现的最小研究样例

以下 SQL 仅为引擎研究样例，不是 Semantic 可执行表达式体，也不是新的 Marivo raw SQL
入口。DuckDB 在内存连接中执行；失败样例各自单独执行。

```sql
-- Mixed exact operands: Decimal(31,2), value 3.75 on DuckDB 1.5.3.
SELECT typeof(a * b), a * b
FROM (SELECT 1.25::DECIMAL(12,2) AS a, 3::BIGINT AS b);

-- Different decimal scales: Decimal(18,5), value 2.65625.
SELECT typeof(a * b), a * b
FROM (SELECT 1.25::DECIMAL(12,2) AS a, 2.125::DECIMAL(8,3) AS b);

-- Independent aggregate state types; the native final division is DOUBLE.
SELECT typeof(sum(v*w)), typeof(sum(w)), sum(v*w), sum(w),
       typeof(sum(v*w)/sum(w)), sum(v*w)/sum(w)
FROM (VALUES (1.01::DECIMAL(12,2), 1::BIGINT),
             (1.02::DECIMAL(12,2), 2::BIGINT)) AS t(v,w);

-- Native Decimal quantile retains input scale; AVG is DOUBLE.
SELECT typeof(sum(x)), typeof(avg(x)), typeof(quantile_cont(x,0.25)),
       quantile_cont(x,0.25)
FROM (VALUES (1.01::DECIMAL(12,2)),(1.02::DECIMAL(12,2))) AS t(x);

-- Conversion before arithmetic loses an integer bit: returns 0.0, not 1.0.
SELECT 9007199254740993::BIGINT + (-9007199254740992::DOUBLE);

-- Expected failure before SUM; a wide accumulator cannot repair the product.
SELECT sum(a*b)
FROM (VALUES (9223372036854775807::BIGINT, 2::BIGINT)) AS t(a,b);

-- A necessary pre-product widening; publication bounds remain separate.
SELECT typeof(sum(a::HUGEINT*b)), sum(a::HUGEINT*b)
FROM (VALUES (9223372036854775807::BIGINT, 2::BIGINT)) AS t(a,b);

-- A common Decimal can have too little integer capacity: expected failure.
SELECT a+b
FROM (SELECT 99999999999999999999999999999999999999::DECIMAL(38,0) AS a,
             0.1::DECIMAL(2,1) AS b);
```

Ibis 构造问题可用仓库解释器独立重现，不需要连接后端：

```python
import ibis

t = ibis.table({"a": "decimal(12,2)", "b": "decimal(8,3)", "n": "int64"}, name="facts")
print(t.a.mean().type())  # decimal(12, 2), unlike native DuckDB AVG
print((t.a * t.n).type())  # decimal(12, 2), unlike native DuckDB product
print((t.a * t.b).type())  # IbisTypeError on Ibis 12.0.0
```

完整 Ibis 执行与结果转换可在内存中重现：

```python
import ibis

con = ibis.duckdb.connect()
con.raw_sql("""
CREATE TABLE facts AS
SELECT * FROM (VALUES
    (1.01::DECIMAL(12,2), 1::BIGINT, 0.5::DOUBLE),
    (1.02::DECIMAL(12,2), 2::BIGINT, 1.5::DOUBLE)
) AS t(v,w,f)
""")
t = con.table("facts")
expressions = {
    "mean": t.v.mean(),
    "ratio": t.v.sum() / t.w.sum(),
    "weighted": (t.v * t.w).sum() / t.w.sum(),
    "decimal_float": (t.v * t.f).sum() / t.f.sum(),
    "integer_mean": t.w.mean(),
}
for name, expr in expressions.items():
    table = expr.as_table().to_pyarrow()
    print(name, expr.type(), repr(expr.execute()), table.column(0).to_pylist(), table.schema)
# mean: execute Decimal('1.02'); Arrow Decimal('1.01'), decimal128(12, 2)
# ratio: 0.6766666666666666, double
# weighted: 1.0166666666666666, double
# decimal_float: 1.0175, double
# integer_mean: 1.5, double
```

研究完成项：官方资料交叉核对、实现责任定位、上述本机数值/类型探针。实施证据如下；
安装包验证和 N5 不属于本轮完成声明。


## 11. Implementation record (2026-10-07)

### Delivered behavior

- Ordinary Metric mean uses Ibis `mean`; weighted mean uses native product,
  paired sums and division. Metric ratio returns float64; Decimal mean retains
  the Ibis-declared Decimal carrier. Sum, extrema and quantiles keep their
  existing native Ibis paths and algorithm identity.
- Value and weight types are independent. Decimal/integer, float/integer,
  Decimal/float and differing Decimal scales are supported on tested routes.
  Decimal/float adapts the Decimal operand because Ibis 12's inferred Decimal
  otherwise disagrees with the backend product. Differing Decimal scales use
  the minimum constructible common Decimal; range failures remain failures.
- Narrow signed integers and floats widen at the measure boundary only. Identity,
  relationship and distinct keys retain their actual source schema.
- Native ratio/linear retain independently typed components, including coordinate
  state. Weighted numerator and denominator retain independently inferred types.
  Source and fixed rollup consume sufficient state, never projected means.
- An explicit standard Ibis scalar cast on division prevents SQL dialect rewriting
  from corrupting a shared retained operand/alias. Forced output casts also handle
  SQLite empty aggregates and backend/Ibis dtype disagreement.
- Native method implementation contracts are version 5 with `native_numeric`
  precision. Store reads retain saved primary values; fresh-process continuation
  uses local retained state and does not bind a source or start DuckDB.
- Downstream comparison error propagation starts at the represented native Cell.
  It does not certify raw-fact aggregation error. Strict paired Cell comparison,
  reference, attribution, Duration and row-statistics policies remain independent;
  mixed Metric composition does not automatically broaden those consumers.
- ClickHouse exact weighted products and retained native sums use necessary wide
  Decimal operations and carrier checks to avoid unchecked integer/Decimal
  wraparound. These backend-specific casts are range protection, not an exact
  rounding algorithm for ordinary aggregates.

### Evidence ownership

`tests/analysis/numeric/test_native_numeric.py` owns mixed inputs, paired nulls,
actual source execution, independent SQL mean/weighted references, coordinate
rollup, narrow-type adaptation and remote ordinary native operations.
`native_numeric_worker.py` owns three-process produce/continue/recover with source
files removed and source binding/embedded execution poisoned. Existing numeric,
coordinate and recovery suites retain empty/zero, Duration, state, consumer and
fault boundaries. Final commands and results are recorded below after completion.

Remote evidence is bounded to Entity, native table and the UTC-us route with the
specific fixture types and operations tested. It is not a full backend/type/storage
matrix, wheel qualification or full Runtime release acceptance. Decimal SQLite,
Duration quantile, nonnumeric output families and wider public carriers are not
newly qualified.

### Expression simplification measurement

DuckDB 1.5.3 / Ibis 12.0.0, one process, 10,000 rows, `n = 1..10000`,
`d = n % 7 + 1`, int64 inputs; no grouping. Compare `_exact_division_value`
(the previous ordinary finish, retained for independent Duration/fold callers)
with `_division_value`, select only the result and export to Arrow. Six runs per
variant: first run reported separately, median of the next five as warm timing.

| Variant | SQL characters | Explicit SQL casts | First run | Warm median | Arrow carrier |
| --- | ---: | ---: | ---: | ---: | --- |
| Previous exact finish | 6,949 | 9 | 26.01 ms | 23.63 ms | double |
| Native Ibis finish | 87 | 1 | 0.90 ms | 0.77 ms | double |

Both variants matched Python division on these bounded operands. This fixture
cannot establish equivalence on extreme integers or cancellation. Combined process
peak RSS was 258,424,832 bytes; it does not isolate per-variant memory. The timings
measure expression execution/export only, not source admission, publication,
remote latency or whole-query performance. No universal speedup is claimed.

The separate cast cleanup is covered by expression/type and Runtime checks; no
independent equivalent-policy performance gain has been established. Precision
policy simplification and redundant-cast removal must not be conflated.


### Validation results

- `make check-agent`: passed; lint, import boundaries, 327-module typing,
  4,765 default tests passed, one existing skip, API documentation built.
- `make runtime-test TESTS='tests/analysis/numeric/test_analysis_numeric.py
  tests/analysis/numeric/test_analysis_coordinates.py
  tests/analysis/materialization/test_numeric_recovery.py'`: 237 passed.
- Native suite with PostgreSQL/MySQL/Trino enabled: 18 passed, two ClickHouse
  cases skipped because that profile was stopped. Includes DuckDB table/Parquet,
  six value/weight pairs, narrow types, mixed coordinate linear, SQLite and
  three-process recovery.
- ClickHouse separately: two passed, covering ordinary mean/weighted/ratio/linear
  source and fixed continuation, plus overflowing retained integer weight sums.
  The overflow case accepts only the explicit carrier-check rejection or a
  wrapped backend `DECIMAL_OVERFLOW` failure, never a published wrapped value.
- Remote tolerance rerun: four passed (PostgreSQL/MySQL/Trino/SQLite), one
  ClickHouse skip. Ratio absolute tolerance is 1e-12, linear 1e-9, Decimal mean
  0.005 for its two-place output carrier; the policy is not inferred by raising
  tolerances after failures.
- After retained-sum lowering changes, the affected composition/Duration Runtime
  rerun passed all 86 cases.
- Affected default numeric/consumer rerun: 302 passed. Final touched-module
  typing, lint/import checks and `git diff --check` passed.

The ClickHouse service was used explicitly, then stopped; originally running
Trino/PostgreSQL/MySQL services were restored and confirmed healthy. No MinIO,
release suite or installed-wheel run was started. These results are overlapping
behavioral scopes and must not be summed as a unique acceptance denominator.

### Adversarial review repairs

The review reproduced three regressions in the N1-N4 implementation:
mixed numeric component attribution assumed homogeneous floating state;
signed linear source rollup could overflow an integer intermediate before a
valid Duration or mixed numeric result; and source linear comparison retained
raw aggregation bounds while fixed comparison used represented Cells.

Attribution now derives numerator and denominator bounds independently and
retains strict floating magnitude checks. Linear finishing widens integer/tick
intermediates before signed arithmetic, or promotes terms to the mixed floating
result carrier first. Decimal intermediates keep the result scale: using
Decimal(38,0) beside Decimal(38,2) made Trino return 0.00 for 1.01 minus 1.
Saved component types and final carrier checks remain.
Source linear comparison now starts at the represented Cell, matching fixed
comparison while preserving allocation, reference and correspondence bounds.
Affected Runtime checks also exposed narrower native Decimal mean differences
being saved with the input precision despite a declared Decimal(38,s) result.
Comparison now widens those operands at its arithmetic boundary, preserving
fixed attribution and source-free cold continuation. The Decimal small-partition
reconciliation refusal remains an independent acceptance check.
Two pre-existing offline attribution cases stopped at obsolete private descriptor
mutation assertions, reproduced on the exact review baseline. Following the
Store 8 trust contract, those assertions were removed while retaining missing or
malformed producer-exchange rejection, source-offline continuation and cold
selected-view recovery. This test repair does not change production behavior.

Focused regressions cover DOUBLE/BIGINT weighted and ratio attribution,
Decimal/integer weighted attribution, integer or Decimal numerators with floating
ratio denominators, Duration nanosecond cancellation, INT64_MIN negation in
floating and Decimal results, a leading integer overflow before a floating term,
final int64/Duration overflow rejection, narrower Decimal mean difference
carriers, and relative change with a tiny finite linear baseline. These are
bounded local DuckDB/Parquet and fixed-continuation checks.

Repair validation:

- Final `make check-agent`: passed; 4,776 default tests passed, one existing
  skip, 327-module typing, lint/import contracts and API documentation passed.
- The 11 new daily component-bound cases and 16 new Runtime regressions passed
  across focused runs and the native suite. Final arithmetic-boundary rerun:
  10 passed. Decimal carrier and small-partition reconciliation rerun: three
  passed.
- Native suite before the two narrower Decimal carrier regressions were added:
  29 passed, five remote opt-in skips. PostgreSQL/MySQL separately passed both
  final reruns; Trino passed after the Decimal scale repair. SQLite is covered
  by the native suite. ClickHouse's two cases were not rerun during repairs.
- The common attribution/comparison selection excluding the 11-case carrier
  matrix passed 98 cases; its two baseline descriptor-mutation failures passed
  after the authorized test repair. Thus all 100 selected cases passed across
  the main run and focused failure rerun. The 11-case carrier matrix was not
  fully replayed after repairs; its affected native Decimal case passed producer,
  source-offline fixed continuation and cold recovery separately.
- Final touched-file lint/import checks and `git diff --check`: passed.

These overlapping scopes are not a unique acceptance denominator. No full
Runtime, installed-wheel, release or object-storage qualification is claimed.
