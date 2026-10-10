# Analysis compare API W1 实施计划

Date: 2026-10-10

Status: W1 已实施并通过本地验收；外部 Runtime 未验证；当前工作区未提交。

Owner: [compare / ratio 设计](../specs/2026-10-10-analysis-compare-api-design.md)第 3–6 节。
Implementation baseline: `main@da65e5b15e`，包含已提交的 W0。

## 1. 目标与范围

一次 `current.compare(baseline, align=...)` 固定返回 absolute / relative 双视图结果。
精确对应固定 checked，不开放 `trust` 或 `verification`；`outer` 默认 `missing="keep"`，
`empty_value` 显式选择。删除 `design`、`value`、`change`、`pairing` 公共选择参数及旧构造入口。

本计划覆盖公共输入、设计推导、结果类型、统一图规则、source/fixed 执行、持久化与冷恢复、
原有数值续接、Help/动态 guidance、测试及 latest 中英文档。W1 完成后停止；
W2 的不等长时间网格与 aligned 输出坐标、W3 的新 τ 工厂另行授权。
Funnel 比较与比值数值政策不变，不增加别名、迁移或双读路径。

W1 period 继续要求完整且等长的两侧网格，输出 current 原桶坐标。
`ordinal()` 只替换位置配对入口；`outer` 在 W1 仍只允许非时间键缺侧。

本文件所在 `docs/superpowers/plans/` 当前被仓库忽略规则覆盖。
将来提交设计修订时，应同时显式纳入本计划，避免设计链接只在本地可解析；本轮不暂存或提交。

## 2. 固定契约与完成条件

```python
comparison = current.compare(baseline)
absolute = comparison.absolute
relative = comparison.relative
result = comparison.execute()
allocation = result.absolute.attribute(axes=(region,))
ratio = revenue.ratio(orders, align=mv.exact())
period = current_grid.compare(prior_grid, align=mv.outer(time=mv.ordinal()))
```

| 项目 | 固定决定 |
|---|---|
| compare 返回 | `LogicalComparisonResult`；执行/恢复为 `MaterializedComparisonResult` |
| absolute 视图 | 现有 Logical/Materialized Difference Relation，`current - baseline`，保留原单位与归因权威 |
| relative 视图 | 现有 Logical/Materialized Numeric Relation，`(current - baseline) / abs(baseline)`，无量纲 |
| 根容器 | 两视图、execute（logical）、有界 show、contract；materialized 另有隔离的 to_pandas 与 Artifact 状态/证据 |
| 数值续接 | 通过明确视图；根没有 value、aggregate、rank、where、attribute、compare、ratio |
| 精确对应 | `verification="check"`；仍使用适用声明/冻结推导，未知对应才检查 |
| 缺侧政策 | `outer()` 等价 `outer(missing="keep")`；`empty_value` 沿用原 Metric 空集 finish 的准入 |
| Cell | 两个必备 Cell 各自保留 tag/reason；非 Defined 传播沿用 W0 |
| 算术 | 两公式独立读取原操作数，精确中间量、各自最终舍入；relative 不读取已舍入 absolute |
| 类型与失败 | 两公式同时准入；Decimal 同 scale，任一视图溢出或 NaN/inf 等数值违例导致整个 Run 失败 |
| 执行所有权 | 一份有序端点/对应域/原量状态，一个原子发布的 Artifact；两视图为类型化读取 |
| 指纹与版本 | 新 compare 根/视图指纹改变；checked ratio 底层规则保持；图 v7 → v8，Store 9 不变 |
| 时间边界 | W1 等长/current 坐标不变；W2 才变为对齐键并升图 v9 |

完成条件是上述契约同时在 source、fixed、source-offline 冷恢复中成立，受影响披露与文档一致，
定向检查和广泛日常 gate 通过。基线测试、source 测试或同进程 restore 均不能代替全部验收。

## 3. 基线与回归依据

开工前核对 `git status --short --branch` 与 HEAD，保留任务外改动。
先读 [Python analysis 设计](../../specs/analysis/python-analysis-design.md)、
[算子与结果](../../specs/analysis/operators-and-frames.md)、
[Session/Runtime](../../specs/analysis/session-state-and-runtime.md)、
[时间契约](../../specs/analysis/timezone-and-calendar-design.md)及
[分析代数](../../specs/analysis/algebra-theory.md)的对应部分；
用 native Help 核实当前 compare/ratio/one_to_one 入口。

修订前已在 `da65e5b15e` 上运行以下旧 API 基线，**17 passed in 28.83s**：

- `test_ordinary_ratio_and_cohort_singleton`。
- `test_period_change_retains_original_buckets_and_rejects_renumbering`。
- `test_one_to_one_binds_exact_ordered_nodes_and_retained_relationship`。
- `test_propagated_cell_priority_is_unknown_undefined_null`。
- `test_null_operand_propagates_in_time_comparison`。

这只是开工前的 W0 与旧 API 基线，不属于 W1 新接口/双视图的验收证据。
基线变动后重跑相关测试，不将历史结果归属于新 HEAD；W1 结果见第 6 节。

对仍准入的端点，按 {time, cohort, period} × {exact checked, outer keep, outer empty_value}
记录旧 difference / relative_change 的数值、类型、单位、逻辑 Cell、原端点及对应。
采用既有 raw-fact/Decimal/Fraction oracle，避免用新生产内核计算新测试的期望值。
仅 checked ratio 与 checked one_to_one（含/不含时间）要求底层定义指纹保持。
旧 assume、不同 scale 的 relative-only 调用以及旧单值 compare 根指纹不属于新等价承诺。

测试遵守现有 `.agents/skills/marivo-test-fixtures/SKILL.md`；
复用 `tests/conftest.py`、`tests/shared_fixtures.py` 与 recovery worker，
不另建重复项目脚手架。逻辑 Cell/证据按 frozen bindings 解码，不以物理列缺省推断状态。

## 4. 实施步骤

### 步骤 1：闭合公共输入与比较设计

涉及 `_comparison.py`、`public_dsl.py`、`materialization/graph_composition.py`、
`graph_relation.py` 及 numeric/graph/typing 测试。

1. 新增 `exact`、`outer`、`ordinal` 与三个具体输入值类型；`outer` 的缺省为 keep。
   `one_to_one(time=...)` 直接接收 `OrdinalTimeKey | None`，覆盖固定 checked。
2. 推导只读取冻结模板、目标 T 与时间绑定 S：time/period/cohort 互斥；
   两条件同时变化、同端点、非法 cohort 域、time 的遗漏或多给均有结构化拒绝。
   静态数值终端限制先于设计推导检查，保留 Duration 行统计等既有拒绝与修复路径。
3. 保留 `period_mapping` 的完整等长检查；测试不重编号 filtered 桶、
   同一时间网格的 cohort 普通时间坐标配对与 ratio 的时间准入。
4. 加入 Logical/Materialized Comparison Result 的具体类型及签名。
   输入和结果遵守 Session 所有权；alignment 与 one_to_one 不能绕过该检查。
   根容器不继承 NumericRelation；两视图及禁止的根操作加入 public typing 检查。

### 步骤 2：复合图与原量权威

涉及 `core/model.py`、`core/rules.py`、`methods/comparison.py`、
`methods/semantics.py`、`methods/builtin.py`、`methods/registry.py`、
`materialization/graph_composition.py`、`graph_relation.py`、`graph_attribution.py`
及 `compiler/graph_attribution.py`。

1. 引入内部闭合 `CellCompare`、`ComparisonRead`、`ComparisonPart`。
   根绑定一个有序端点对、design、alignment，quantity 为 None；
   两个 read 各有具体 Signature/单位/物理类型，field 只允许 absolute/relative。
2. 同时校验两算子的准入：Decimal 同 scale；int/duration/float 输出类型与既有各算子一致。
   原始端点、CorrespondencePart、Subject 与合法原量状态只保留一份，
   两个 Cell 及其浮点误差界分开描述。
3. 递归模板/捕获、absolute 归因与筛选从冻结父节点解析确切原端点。
   absolute 保留现有完整状态和 exact 准入；relative 不因根保留状态而获得 rollup/share/归因能力。
   nested compare 经明确视图进入，其独立准入不放宽。
4. 注册统一图规则与 required parts，不通过列名猜测权威，
   不创建两个独立 compare 再拼表，也不增加专用执行器或快照。

### 步骤 3：source 与 fixed 双视图执行

涉及 `compiler/graph_plan.py`、`graph_lowering.py`、
`materialization/graph_source_execution.py`、`graph_local_execution.py`、
`graph_exchange.py`、`graph_observation.py`、`methods/local.py`
及 comparison runtime/consumer/non-defined 测试。

1. 在同一个配对域建立一次对应义务，完成存在性/空集政策和有限数值检查，
   然后由同一原端点计算两公式。保留声明/推导免检的现有 Evidence 来源；
   未知 exact 键像必须检查，不能缩为 inner intersection。
2. source 与 fixed 统一两 Cell 布局及每字段误差界；空域也必须保留精确 schema。
   两视图使用同一对应投影与原 reason，访问 materialized 视图不分配 Run。
3. 用下表锁定独立 Cell 和共同失败边界；对适用情形分别运行 source/fixed：

| 情形 | 预期 |
|---|---|
| baseline 0，current 10 | absolute Defined(10)，relative Undefined(zero_baseline)，Run 成功 |
| baseline -100，current -50 | absolute 50，relative +0.5 |
| Decimal / 大整数 / duration | 各视图精确类型与原公式一致，relative 不使用舍入后的 absolute |
| Decimal scale 不同 | 在构造准入时拒绝，即使旧 relative-only 支持 |
| absolute 溢出、relative 可表示 | 整个 Run 失败，无半个成功 Artifact |
| relative 溢出、absolute 可表示 | 整个 Run 失败，无半个成功 Artifact |
| 非 Defined 与双非 Defined | 分别传播；优先级 Unknown > Undefined > Null，保留两侧原 reason |
| outer keep 缺侧 | 两视图均 Undefined(missing_side)，对应 presence 正确 |
| outer empty_value | 仅合法原 Metric finish，含 Defined(0)/Null/Undefined；非法原量拒绝 |
| exact 双空 / 键像不同 | 双空合法；未知键像不同拒绝，不发布交集 |
| NaN / inf | 保留整个 Run 的失败政策 |

4. 对 source 与 fixed 两条路径的结果比较使用逻辑 Cell 表。
   共享范围仅为单次 invocation；不承诺 native 检查/parts 单次扫描或事务快照，
   不增加跨 Run 缓存。
5. source 视图的筛选与行统计沿用现有 Arrow 消费内核及独立检查收据；
   `absolute.where(...).members().observe(...)` 通过既有 prepared observation
   绑定原 Subject envelope，保留重新观察后的行均值及原量 rollup。
   归因视图的比值、排序和组合表继续使用已准入的本地消费路径；
   周期覆盖检查保留对应义务与前驱完成收据，不回退为未检查的局部 SQL。
   轴扩展归因的完整键配对收据由已完成校验的内核登记，数值载体不改变该检查。

### 步骤 4：单 Artifact、恢复与完整性

涉及 `materialization/graph_dataset.py`、`graph_snapshot.py`、`graph_value_tables.py`、
恢复 wrapper/descriptor 分派、`public_dsl.py` 及 materialization 测试/worker。

1. shared codec 增加闭合规则/parts；图 schema 与前缀同步升为
   `marivo.analysis.graph_dag/v8` / `graph-dag-v8:`。统一更新版本断言，
   Store generation 仍为 9；旧图要求源重新执行，不加双读或迁移。
2. primary 两个 Cell、对应/状态 required parts、Evidence 与成功终态一起提交；
   中途失败不得提交一个可恢复的部分 ComparisonResult。
3. `session.artifact(ref)` 返回 MaterializedComparisonResult；两视图只是同一 Artifact 的类型化投影。
   复用 existing native snapshot/restore，不追加第二份端点或自定义 sidecar。
4. recovery worker 在独立进程、源不可访问的条件下读取根与两视图：
   核对类型、键、数值、tag/reason、Evidence 与 retained state，
   并验证 absolute 归因/筛选、relative 数值筛选及适用的 nested 续接。
5. 完整性负例覆盖任一 Cell/类型/必备 part 缺失、投影键/顺序/对应篡改、
   端点/状态不一致；正确的零基线 Undefined 不可误判为完整性失败。

### 步骤 5：披露、调用点与拥有者文档

涉及 `__init__.py`、`_public.py`、native disclosure/capability registry、
typing/surface 测试、仓库调用点及 current specs / latest 站点。

1. 删除七个旧导出，新增三个工厂、三个输入类型、两个 Result 类型；
   Difference/Numeric Relation 保留作为视图。`ComparisonResult` 不作为公共别名，
   新内部规则/parts 不进顶层 Help。
2. public docstring 覆盖用途、参数、具体返回类型、可运行示例与约束。
   更新 Help 的静态事实、Result 的动态 contract/show 与结构化修复；
   根只给视图续接，独立 reachability/drift/budget 测试与 `__all__` 快照同步。
3. repr 有界单行，带类型/身份/design 并指向 show；
   logical show 只读冻结定义，materialized show 同时显示两 Cell 与差异捕获。
   根 to_pandas 导出键及 absolute/relative 和各自 tag/reason，
   视图沿用单值导出；验证返回副本不影响原结果。
4. 全仓搜索旧类/工厂/关键字，逐点迁移测试、worker、packaging journeys、示例和错误修复，
   区分 Funnel 自有 compare。旧参数只保留必要的移除断言，不保留兼容行为测试。
   assume 测试改为 checked 的未知对应检查，不删除其他合法 Evidence 来源测试。
5. 更新 `algebra-theory.md`、`operators-and-frames.md`、`python-analysis-design.md`、
   `session-state-and-runtime.md`、`timezone-and-calendar-design.md`、
   `docs/specs/temporal-semantics.md` 与 `semantic-object-model.md` 的对齐描述。
6. 更新 `site/src/content/docs/docs/latest/` 与 `site/src/content/docs/zh-cn/docs/latest/`
   的比较/归因、troubleshooting 及所有受影响例子，给出 relative 负基线例子。
   authored API 入口 `docs/api/analysis.rst` 同步；历史版本、site/dist、生成 API 文件不手改。
   packaged skills 当前没有旧符号依赖；若发现必须修改，另行请求明确批准。

## 5. 验收入口与证据边界

迭代时先选失败行为所在文件或 test node，再逐步扩大：

```sh
make test TESTS='tests/analysis/numeric/test_comparison_result.py tests/analysis/numeric/test_comparison_non_defined.py tests/analysis/graph/test_premise_verification.py tests/analysis/graph/test_domain_contract.py'
make test TESTS='tests/analysis/numeric tests/analysis/graph tests/analysis/materialization tests/surface tests/typing'
make typecheck TYPECHECK_TARGETS='marivo/analysis tests/typing'
make lint-agent LINT_TARGETS='marivo/analysis tests/analysis tests/surface tests/typing tests/packaging/relation_journeys.py'
make slow-test TESTS='tests/analysis/numeric/test_analysis_comparison_runtime.py tests/analysis/numeric/test_comparison_non_defined.py tests/analysis/materialization/test_comparison_recovery.py tests/surface/test_analysis_context_budget.py tests/analysis/numeric/test_comparison_result.py tests/analysis/numeric/test_analysis_attribution_runtime.py tests/analysis/numeric/test_analysis_display.py'
npm --prefix site run verify:content
make check-agent
git diff --check
```

`make check-agent` 是完整日常 gate，包含 lint/type/default tests/API docs，
默认排除 slow/runtime/release；不得把它当作冷恢复或 Runtime 验收。
定向 slow 覆盖独立 fixed/cold 与预算；对应文件的默认用例由前面的 `make test` 覆盖。
如修改触及 runtime 标记的比较路径，以 `make runtime-test TESTS='实际受影响文件或节点'`
另跑，并记录 backend 与结果；服务不可用时准确报告未通过的范围。
本任务不运行全量 Runtime/release-check，也不为普通实施启动 MinIO。
packaging 调用点必须迁移，但不宣称 wheel/installed 资格；
`make release-test` 不接受 TESTS 缩小完整套件，不用于本任务窄验收。

结束时记录最终 HEAD/工作区范围、通过/失败/未运行项，分别列 source、fixed、cold 的证据。
只有步骤 1–5、必须 gate 与文档同步都完成，才标记 W1 已实施；
实际验收结果见第 6 节，不将上述计划命令本身视为通过证据。

## 6. W1 完成记录

验收日期：2026-10-11（Asia/Shanghai）。当前分支为 `main`，HEAD 仍为
`da65e5b15e`；以下结果对应其上的 W1 未提交工作区，不是已提交的 W1 SHA。
本轮未暂存、提交或推送，未修改 `AGENTS.md` 或 packaged skills。

步骤 1–5 已完成：公共入口固定 checked 与 absolute/relative 双视图，
`outer()` 默认 keep；统一图中以 `CellCompare`、`ComparisonRead` 和 `ComparisonPart`
承载共享对应与独立 Cell。图 schema/前缀为 v8，Store generation 保持 9。
调用点、native Help/动态 guidance、API 文档及 latest 中英文站点已同步。
W1 等长网格/current 输出坐标边界保持；W2、W3 未实施。

### 执行证据

- **source**：DuckDB table/parquet 用例以 fixture 原始事实及 Fraction/Decimal oracle
  验证数值、单位、Cell 与原端点；覆盖整数、浮点、Decimal、Duration 载体，
  exact/outer、零/负基线、独立最终舍入、双视图共同失败及归因轴扩展。
  source 筛选、行统计、归因视图的比值/排序/表组合及重新观察续接通过。
- **fixed**：先独立执行两端，再构造比较；验证两视图读取同一 Artifact、
  读取不分配 Run、证据一致、to_pandas 返回隔离副本，以及合法归因/数值续接。
  任一视图溢出都使整个 Run 失败，不发布部分成功结果。
- **cold**：独立进程恢复，在业务源与模型目录离线、相关源/semantic 访问被禁止的条件下，
  验证根与两视图的类型、值、状态和续接；包含 absolute 归因及 relative 筛选。
  同进程 restore 另有覆盖，不用于替代此证据。

### 最终检查

| 检查 | 实际结果 |
|---|---|
| `make check-agent PYTEST_FLAGS='-q --tb=short --maxfail=5 -n 2'` | 全范围 lint/import、349 个模块 typecheck、3485 passed / 1 skipped、API docs 构建成功；测试 178.27s |
| `make test TESTS='tests/analysis tests/surface tests/typing' PYTEST_FLAGS='-v --tb=short --maxfail=5 -n 4'` | 3390 passed / 1 warning，833.56s |
| 第 5 节七文件 `make slow-test`，`PYTEST_FLAGS='-v --tb=short --maxfail=5 -n 2'` | 181 passed，496.32s；含扩展归因/展示、fixed/cold 与 Help 预算 |
| 数值载体的 source/fixed/轴扩展归因节点 | 7 passed，258.87s |
| Journey 数值终端拒绝节点 | 3 passed，58.02s；原数值准入先于设计推导 |
| `npm --prefix site run verify:content` | 385 required files 通过 |
| 本设计与本计划的链接/示例检查 | 9 个本地链接存在，7 段 Python 示例通过 AST 语法检查 |
| `git diff --check` | 通过 |
| `make runtime-test TESTS='tests/analysis/graph/test_distribution_consumers.py'` | 68 skipped，3.90s；外部服务 opt-in 未启用，不属于真实后端通过证据 |

广泛检查的 warning 是既有 fixture 使用 `fetch_arrow_table()` 的 DeprecationWarning。
并行检查中曾有中断用例进入测试救援路径；该节点独立复测及最终四 worker 全套均通过，
本轮未修改中断实现或放宽断言。

未运行全量 Runtime、wheel/installed 或 release 资格检查，未启动 MinIO。
上述三个测试套件存在重叠，不合计为独立测试总数。
本计划仍受仓库忽略规则覆盖；未来提交时需要显式纳入，保持设计链接可解析。

### 本地审查修复（2026-10-11）

已修复两项 P2：双 Cell 校验在构造单视图契约时同步投影 schema 和 required parts；
absolute/relative 的 logical/materialized `contract()` 恢复 `where` 与 `aggregate` 入口。
原有公开契约不变。回归测试先复现失败，再验证紧凑与逻辑格式的合法结果均通过，
并分别锁定 absolute/relative 值篡改、物理类型错误及缺失 comparison part 的拒绝原因。

- 定向 `test_comparison_result.py`：11 passed / 1 deselected，12.81s。
- 相关慢测试（双视图与 comparison recovery）：13 passed，53.10s；包含离线冷恢复。
- 完整 `make check-agent PYTEST_FLAGS='-q --tb=short --maxfail=5 -n 2'`：
  lint/import、349 个模块 typecheck、API docs 构建通过；3485 passed / 1 skipped /
  1 warning，118.45s。warning 来自 Ibis `Comparable.__del__` 的缓存 KeyError，
  不作为已修复或已定位根因的证据。
- `git diff --check` 通过。本轮未重跑外部 Runtime、wheel/installed 或 release 检查，
  未暂存、提交或推送。

本节记录审查修复后的验收；前一节的扩展分析与扩展慢测试结果属于修复前工作区。
