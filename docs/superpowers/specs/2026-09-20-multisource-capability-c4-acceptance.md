# C4：受限行表达式、Linear graph 与可解析精度 Decimal 验收

日期：2026-09-20。状态：C4 已完成（逐后端开放/拒绝单元见 §2 矩阵；MySQL decimal mean/div 与 Trino 全部 decimal 单元保持拒绝，证据见 §3/§4）。依据 [实施计划](2026-09-19-multisource-capability-c4-implementation-plan.md)、[总计划](2026-09-16-multi-datasource-capability-completion-design-and-plan.md)、[C0 清单](2026-09-16-multisource-capability-c0-implementation-plan.md) 与 [C3b 验收](2026-09-19-multisource-capability-c3b-acceptance.md)。

实施基线 `62ccfd252`（C3b 后），验收代码 `04391c784`。提交链 `c63ca8dc8..04391c784` 共 11 个提交：计划 1、语义归一化 2（含结构化 cast 界修复）、执行下推 2（含 value-exact cast 规则）、准入 2（含 ratio-root flag 清理）、rollup 1、披露 1、整体审查修复 2。本记录仅覆盖 C4，不表示 C5、C6–C9 或 C10 完成。

## 1. 交付范围与实施修正

三项交付全部落地：

1. **计算型 Measure（受限行表达式）**：`@ms.measure(...)` 单返回 ibis 行级体在六后端可被聚合。语义层新增 op-tree 推导（`metric_graph_lowering.py::_computed_measure_type` + `_derive_expression_type`）：拒绝 Reduction/窗口/跨表/未声明列/未解析 decimal/int 操作数 cast 超界；执行层 `_measure_column` 泛化为返回 `ir.Value`（`evaluate_expression_body`，同一次表扫描，实测编译为过滤子查询上的 `SUM("amount" * "quantity")`，不新增源查询）。
2. **Linear graph**：`LinearNodeV1` 六后端准入；系数 ±1 的类型精确乘法落地四处消费点（`metric_graph_lowering.visit`、`lowering._values`、`lowering._fold_value`、`rollup._value`），int64 项保持 int64、decimal 项保持 Decimal、混合项按引擎 warm 行为提升 float（与 warm 相等绑定一致）。非 ±1 系数在持久化层（`metric_graph_canonical.py:513`）与 runtime 层双重拒绝。
3. **可解析精度 Decimal**：单一规则 owner `marivo/semantic/decimal_precision.py`（`DecimalType` + add_sub/multiply/sum_of/min_max，None=拒绝）；逐单元准入 `resolved_decimal_units: frozenset[Literal["linear","mean","div"]]`；传输层 `_declared_cast(value, logical_type, declared_decimal=...)` value-exact 规则（scale 收窄或整数位收窄 → 结构化失败；保值归一 cast 允许）。

计划相对落地的五项已批准修正（审查确认记录一致）：

- **修正 A（行表达式规则表扩展）**：计划 §3 规则表未覆盖整数字面量/int 列。实测 ibis 与 DuckDB 一致把 int 操作数提升为 `dec(19,0)`，语义 walk 采用同一提升；整数字面量推导为 `dec(1,0)`（值精确，已知现象，见 §6）。
- **修正 B（传输 cast 规则具体化）**：计划"传输 p/s 精确一致"与 §4 开放单元矛盾（DuckDB mul 物理 DECIMAL(18,4) vs 推导声明 dec(24,4)）。落地为 value-exact 归一：仅允许一步保值 cast（整数位不收窄、scale 不收窄）；dec(24,4)→dec(20,4) 类值位置收窄有单元测试证明拒绝。
- **修正 C（MySQL mean/div 保持关闭）**：计划 §4 矩阵该两单元计划开放，实施期证明不可开放——(1) mean pipeline 以 float 标注的 `sum/count` 除法发布，`_declared_cast` 拒绝；(2) div 单元在当前可构造 graph 中不可达（engine div 推导标 float64）。Task 4 的 live 探测（§4）进一步证明 bit-exact 契约不可达。§3 的 mean/div 规则函数未实现（无消费者，YAGNI）。
- **修正 D（条件/NULL 处理未开放）**：计划 §1 与 §6 提及 `coalesce`/`ifelse` 行；语义 walk 将其按"cannot be derived"拒绝，披露文本（design spec、双语 site）如实记载白名单为 add/sub/mul、一元取反、显式 cast、类型化字面量。计划 §6 对应正向用例未覆盖。
- **修正 E（Trino 以实测为准）**：live 探测证明 AVG 在输入 scale 处有损（10.005→10.01）→ Trino 行表达式与 Linear 开放，全部 decimal 单元关闭。探测原始类型记录于 `trino_support.py` docstring 与 Task 3 报告。

## 2. 六后端单元矩阵（验收状态）

| 后端 | 行表达式 | Linear | decimal linear | decimal mean/div | 保持拒绝（既有+本轮） |
| --- | --- | --- | --- | --- | --- |
| DuckDB | ✅ 运行时验收 | ✅ 运行时验收 | ✅ 运行时验收 | ❌ AVG/div DOUBLE | tier-2 body；条件/NULL 体 |
| PostgreSQL | ✅ 运行时验收 | ✅ 运行时验收 | ✅ 运行时验收 | ❌ numeric scale 非公开契约 | 同上 |
| MySQL | ✅ 运行时验收 | ✅ 运行时验收 | ✅ 运行时验收 | ❌ 修正 C（live 证据 §4） | `div_precision_increment≠4` 执行守卫其余单元 |
| SQLite | ✅ 运行时验收（int64/float64/string） | ✅ 运行时验收 | —（无 decimal） | — | decimal 全部（既有） |
| Trino | ✅ 运行时验收 | ✅ 运行时验收 | ❌ 修正 E | ❌ 修正 E | 未实测通过的全部 decimal 单元 |
| ClickHouse | ✅ 运行时验收 | ✅ 运行时验收 | ✅ 运行时验收 | ❌ div 截断/AVG Float64 | 内部宽累加不视为公开精度（既有） |

DuckDB 无 `*_support.py`：其矩阵行由语义规则表 + `_declared_cast` 拥有（本节为计划 §8 要求的归属声明）。"unqualified computation"、"only direct-column measures" 及整体 composed-Decimal 拒绝文本已按单元分流；准入保持静态纯函数，`div_precision_increment=4` 在执行期守卫（`mysql_execution.py::require_div_precision_increment`，一次性 `@@div_precision_increment` 读取，触发于含 decimal Divide 的编译表达式）。

## 3. 独立预期值与证据形态

全部期望值为手算常量，引擎仅作对照：

- 计算型 sum：`15.75×3 + 4.25×1 = Decimal("51.50")`（DuckDB/PG/MySQL/ClickHouse live 一致）；`amount − dec(3,2) 字面量` 的 sum/min/max = `18.00/3.25/14.75`。
- 加权均值 over 计算型体：`(47.25×3 + 4.25×1)/4 = 36.5`（真实执行路径覆盖两个 unwrap 位点）。
- 冷进程三进程等值（本地）：decimal linear `54.14`、int64 linear `3`（dtype `int64[pyarrow]`）、float mean `5/3`；源库改名后零源查询，dtype 精确断言（`decimal128(38,2)`/`int64`/`double`）。
- rollup 纯函数：Decimal linear 精确（修复前 RED 值 `26.299999999999997`）、int64 `type(result) is int`、0.5 遗留 float 路径、混合项 float 提升。
- 传输：dec(24,4)→dec(20,4) 拒绝、dec(18,4)→dec(24,4) 归一、dec(12,2)→dec(12,0) scale 收窄拒绝。

负向邻接项同批验收：体内 `sum(...)`/窗口/跨 Entity/未声明列/未解析 decimal/cast 超界 → 语义加载期结构化拒绝（两类 cast 超界形状有回归测试）；非 ±1 系数持久化与 runtime 双拒；已声明未使用列、既有 float ratio/weighted mean、日历/累计/count_distinct/quantile/Event/Lifecycle 行为不变（fold matrix/scalar admission/retained runtime 全绿）。

## 4. MySQL decimal mean 的 live 探测（修正 C 的决定性证据）

服务：MySQL 8.4.11（tests 管理的 analysis 容器，reader/admin 只读探测，验收后停止）。精确 5-tie 用例：三行值使 sum/count = `10.01/32 = 0.3128125`（s+4 处恰为 5-tie，HALF_EVEN 与 HALF_UP 必然分歧）：

| 方法 | 结果 |
| --- | --- |
| engine `AVG(col)` | `0.312813`（ROUND_HALF_UP） |
| server `SUM/COUNT` | `0.312813`（同向，堵死"冷端方程修复"退路） |
| 规定 HALF_EVEN 量化 | `0.312812` |

bit-exact 契约不可达 → mean 保持关闭；warm==cold==engine 的三进程等值测试以 float mean 形态验证机制（真实通过），decimal mean 单元记为剩余拒绝项。原始七行探测表在 Task 4 报告（`.superpowers/sdd/c4-task-4-report.md`，过程证据）。

## 5. 验证命令与结果

最终提交 `04391c784` 上复现（整体审查子 agent 独立复跑确认）：

- `make check-agent`：exit 0，5586 passed / 16 skipped（含 API 文档检查；较修复前 +2 为新增回归测试）。
- C4 静态套件 131 passed / 4 skipped；runtime 标记 C4 套件 10 passed / 9 skipped（未启用后端按 opt-in 正确 skip）。
- `make typecheck`（263 files）与 `make lint-agent`（含 import-contracts）清洁；`npm --prefix site run verify:content`（343 files）与 `npm --prefix site run build` 通过；`git diff --check` 通过。
- live 后端轮（实施期执行）：PG 42 passed、MySQL 40 passed / 6 skipped、ClickHouse 43 passed、Trino 42 passed、本地 DuckDB/SQLite 18 passed。每后端含计算型 sum + decimal linear 的真实执行。

计划 §7 的两处命令级偏差（修正记录，不改历史计划文本）：(1) 计划写的 fixture 名 `tests/lazy_computation_fixtures.py` 实际演化为 `tests/lazy_rollup_equation_worker.py`，对应 lint 目标随之改变；(2) MySQL 命令行缺 `MARIVO_MYSQL_ANALYSIS_TEST=1` 前缀（tests 按 opt-in 正确 skip，未产生误通过）。

## 6. 已知限制与后续跟进（按整体审查 triage 记录）

- **MySQL decimal mean/div、Trino 全部 decimal 单元、其余后端 decimal mean/div/AVG 保持拒绝**（各拒绝点诊断均给出引擎事实；`mysql_support.py` docstring 与本记录 §4 一致）。
- **`div` Literal 成员是前瞻契约**：当前无 decimal 根 ratio 可构造，`div_unit` 的不可达分支为防御性；未来 decimal-rooted ratio 形态在 MySQL 上无需新增 opt-in 即按规则判定。
- **字面量常量体推导 `dec(1,0)`**：值精确；披露一句话记录。
- **int 提升宽度不敏感（一律 `dec(19,0)`）**：对全部 int 操作数保值；"最小宽度"为改进项而非缺陷。
- **双 owner 隐患（跟进重构）**：`_measure_decimal_facts`（analysis）经 ibis 解析声明类型字符串，语义 walk 用显式 `decimal_fields` 映射；两者今日按构造一致，若表示漂移则分歧。建议后续让 analysis 消费 walk 的已解析事实。
- **`_linear_coefficient` 规则三处手工同步**（lowering/rollup/semantic walk）：今日交叉注释正确；第四处出现时应提取单一 owner。
- **rollup float+Decimal 混合测试未断言 `type(result) is float`**；count over decimal 减法体未单独覆盖（通用 count-over-decimal 路径已执行）；`_declared_source` 私有 fixture 导入（测试卫生）；冷进程测试 TZ pin 与主机时区相同（本机未构成真正位移；计划"主机 TZ 改变"的字面由 worker 的独立进程 + 源不可用覆盖）；两处措辞（"these backends" vs "these new backends"、错误文本 "decimal operand decimal on multiply"）。以上均为 polish 级。
- **两个真实单元测试位于 runtime 标记文件内**（div_precision 守卫、DuckDB mean 拒绝）：默认门禁 skip，`-m runtime` 下通过；范围卫生项。

## 7. 披露对齐

`docs/specs/analysis/python-analysis-design.md` 排除清单移除 linear（保留 cumulative），新增计算型 Measure 白名单段与逐单元 Decimal 决策段（含四引擎事实）；`_disclosure.py` 的 `runtime_metric.linear` 指导更新（类型行为子句区分整数/Decimal 项与混合项 float 提升）；双语 site latest（EN + zh-cn）排除清单改写且事实逐条一致（EN/ZH parity 经审查逐行核对）；packaged skills 全量只读排查，零失配，无需批准项。预算未上调（linear 页 17/104 行、1660/9000 字符）。

## 8. 审查

本轮按 subagent-driven 流程执行：五个实施任务各经独立规格+质量审查（Task 1 两轮、Task 2 两轮、Task 3/4/5 各一轮），最终整体审查（全分支 diff + 计划逐条对齐 + 跨层一致性 + 遗留 triage）。审查实际拦下的问题包括：未解析 decimal 操作数的裸 TypeError、`_declared_cast` 收窄规则与 docstring/计划矛盾（物理 dec(24,4)→声明 dec(20,4) 曾被静默接受）、MySQL mean/div 矩阵单元与执行现实的冲突、cast 超界的裸 ValueError 与次序依赖诊断、`mysql_support.py` docstring 与修正记录的事实矛盾（两处实施方陈述与代码相反均被证伪：一次报告描述反置、一次声称 import 合并未执行）。

## 9. 环境

未启动 MinIO、未运行 release-check、未发布、未推送远端。服务变更：Trino 启动→探测→停止（Task 3）；MySQL analysis 启动→mean 探测→停止（Task 4）；其余保持原状。执行回执沿用既有 `-execution-receipts.json` 体系，本轮未覆盖 `multisource-slice-N` 记录；Task 3/4/5 报告（`.superpowers/sdd/c4-task-{3,4,5}-report.md`）为过程证据。
