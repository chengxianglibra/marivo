# Correlation Statistics 实施计划

Status: planned，2026-10-20；尚未实施或验收。本轮仅新增计划文档。

## 1. 依据与交付边界

目标合同为 [Correlation Statistics 设计](correlation-statistics-design.md)，包括最新
C11 pending 输入共享求值见证。现有行为以 [AnalysisFrame](query-result-reuse.md)、
[Python Analysis Design](python-analysis-design.md)、
[Operators and Frames](operators-and-frames.md) 及
[统一执行合同](lazy-analysis-dsl-execution.md#111-共享逻辑节点与执行内输入) 为准。
本计划分配代码与验收责任，不重定义设计中的算法、类型或样本政策。

起草时 HEAD 为 `9f2d92508e1a569cb2de5c3504b3a75581e5f0e5`，设计文件尚未提交；
其 SHA256 为 `7d946a327539e1bd45705f84a6d21b518a7ca2831d5c6717d5b30d0d92f717a0`。
实施开始时重新记录 HEAD、设计版本与工作区差异，保留其它未完成工作。

交付：

- AnalysisFrame.correlate 是常规入口，惰性返回普通 Frame，
  `output_names == ("coefficient", "n_pairs")`。
- Column.corr / Column.pair_count 返回既有私有 _Statistic 封装，供 reduce 组合；
  reduce 的公开签名、命名规则及单项单列合同保持不变。
- 两层共享数值内核、分组域与检查义务；一个 correlate 请求在一次输入求值上取得
  系数和计数，独立 pair_count 不计算系数或排名。
- 同步三个 Help 叶子、类型导航、错误、示例、API 文档及 latest 英中文档。
- C01–C11 有明确结果与证据，最终候选通过未缩小的 `make check-agent`。

范围限于当前行上的 Pearson/Spearman；不新增 Session 入口、结果子类型、
规约自动展开、来源相关统计下推、执行器、缓存或 Store 格式。实现不依赖新装 SciPy；
排名与稳定计算遵循设计 §5.2，使用项目已有依赖及标准库。

## 2. 代码落点与执行链

### 2.1 一条执行链

```text
Column.corr / Column.pair_count -> closed two-input statistic -> _Statistic
AnalysisFrame.correlate        -> one ReduceValues with two named statistics
AnalysisFrame.reduce           -> the same ReduceValues
    -> Frame schema and concrete type preparation
    -> frame_plan / FrameProgram / FrameCompute
    -> existing scheduler and shared source/fixed input
    -> frame_values grouping + correlation numeric kernel
    -> ordinary completed Frame -> existing persist / cold read
```

correlate 校验接收者的两个 Column，复用现有 reduce 构造一次规约；不得分别 compute
系数和计数后拼接。无须增加 Correlate 节点或单独的结果协议。错误保留具体端点、
expected/received/repair 和实际入口上下文，不丢弃上游严格子式的错误事实。

### 2.2 主要改造与消费者核对

以下是职责落点，不要求机械修改每个文件。新文件明确标为拟新增；其余均为现有路径。

| 落点 | 本次责任 |
| --- | --- |
| [dsl/column.py](../../../marivo/analysis/dsl/column.py) | 添加 corr/pair_count；复用精确 input/Session 绑定；只接受 numeric 或待解析输入，已知 Duration 立即拒绝；_Statistic 承载闭合统计联合，sum/mean/row_count 行为保持 |
| [dsl/frame.py](../../../marivo/analysis/dsl/frame.py) | 添加 correlate；固定构造 coefficient/n_pairs 两项规约；复用 _require_column、_bind_statistic、完整轴检查；扩展 _operation_summary 展示方法及有序双端，不求值 |
| [_definition/query.py](../../../marivo/_definition/query.py) | 在既有 Statistic 之外增加私有双输入统计变体，left/right 必需，方法闭合为 pearson/spearman/pair_count；ReduceValues.values 接受闭合联合，不向原单输入类堆可选字段 |
| [core/frame_expression.py](../../../marivo/analysis/core/frame_expression.py) | expressions 遍历双端；pending 与 concrete schema 明确固定输出类型、nullable 和 unit；两端分别检查类型/单位，不要求单位相等；准备阶段解析为 Duration 仍拒绝 |
| [compiler/frame_plan.py](../../../marivo/analysis/compiler/frame_plan.py)、[compiler/query_program.py](../../../marivo/analysis/compiler/query_program.py) | 为双端保留各自的实际输入 dtype，不能沿用一个 statistic_types 项代表两端；保留单输入/计数分支，继续生成普通 FrameCompute |
| [materialization/query_runtime_types.py](../../../marivo/analysis/materialization/query_runtime_types.py) | effective_frame 仍按本次消费的 StageOutput 重建 frame_program；确认实际 int/float/Decimal 类型变化不会覆盖 corr/count 的固定输出 schema |
| [methods/frame_values.py](../../../marivo/analysis/methods/frame_values.py) | 沿用现有完整键分组、零轴空输入、规范键排序和 presence 删除；先求值两端并履行检查，再在各组建立共同掩码；独立计数直接取掩码数量 |
| `marivo/analysis/methods/frame_correlation.py`（拟新增私有模块） | 承担有效配对向量的精确常量判定、平均秩及设计 §5.2 的数值计算；返回普通系数/NA，不拥有 Frame、分组、Session、读写或缓存 |
| [compiler/optimization.py](../../../marivo/analysis/compiler/optimization.py) | 检查槽依赖、必要检查与剪列同时覆盖双端；保留现有禁止无证明规约合并的边界 |
| [materialization/query_key_fusion.py](../../../marivo/analysis/materialization/query_key_fusion.py)、[source_query_policy.py](../../../marivo/analysis/materialization/source_query_policy.py) | 新统计明确不取得旧来源规约下推资格，包含 DuckDB 路径；拒绝融合后继续已有本地 Frame 路线 |
| [methods/query_values.py](../../../marivo/analysis/methods/query_values.py)、[materialization/query_runtime_types.py](../../../marivo/analysis/materialization/query_runtime_types.py) 的 ResultProgram 分支 | 核对共享 ReduceValues 联合的旧消费者；按变体收窄或显式拒绝不支持的路线，不能把相关统计误当 sum/mean，也不复制第二套相关算法 |

现有 `_definition/graph.py` 的 dataclass 编码可保留方法及有序字段；核验其遍历和
指纹覆盖双端即可，不建立按对称性归一化的新缓存键。correlate 与手写 reduce
不要求相同表达式身份或指纹。

[frame_evaluation.py](../../../marivo/analysis/materialization/frame_evaluation.py)、
[graph_source_execution.py](../../../marivo/analysis/materialization/graph_source_execution.py)
及 [graph_execution.py](../../../marivo/analysis/materialization/graph_execution.py)
继续拥有准备、共享读取、执行和释放；
[graph_local_execution.py](../../../marivo/analysis/materialization/graph_local_execution.py)
的 run_local 仍经 effective_frame 调用 frame_values。通常无需改动这些调度器；
C11 验收其接入结果。
持久化沿用普通 float64/int64/NA 列及完整轴目录，不添加样本、排名或可合并摘要。

## 3. 阶段、依赖与退出条件

当前所有阶段及 C01–C11 均为待实施。以下为工作责任，不预设多 agent 或分支策略；
同一实施者可顺序完成。实施时在此表补充实际状态和证据位置，避免另建重复台账。

| 阶段 | 责任与主要文件 | 依赖 | 退出条件 |
| --- | --- | --- | --- |
| S0 | 核对设计、基线及共享 Statistic 消费者 | 本计划 | 确认工作区范围、验收归属和实际修改列表；不重新打开已确定的公开签名 |
| S1 | 表达/类型：query.py、column.py、frame_expression.py、frame_plan.py、query_program.py | S0 | 双端捕获、类型准备、固定输出 schema 闭合；C01/C02/C06 的构造与结构部分通过，修改模块类型检查通过 |
| S2 | 执行/数值：frame_values、frame_correlation、实际类型与优化消费者 | S1 | C02–C08 的底层统计见证通过；零配对组和独立掩码保持；旧路径不会误收新统计 |
| S3 | 高层入口：frame.py；pending show、共享求值与普通 Frame 续算 | S2 | C01、C11 完成，C10 保存恢复增量通过；两层没有第二套算法或独立读取 |
| S4 | 公开披露：docstrings、Help 描述、类型/导出约束、文档 | S3 | C09 通过；常规与组合入口的例子实际可执行，英中和 API 文档一致 |
| S5 | 集成验收与收尾 | S1–S4 | C01–C11 全部有结论；最终源码及未缩小的 check-agent 通过；无未解决 P2+，证据分层明确 |

### S1–S2 的实现要点

- 双输入类型与遍历一次补齐：搜索所有 `.value`、`statistic_types`、Statistic 和
  ReduceValues 分支，逐个区分单输入、计数与双输入。不要依靠强制 cast 掩盖遗漏。
- 类型准备必须验证两个端点；pending corr 固定 numeric/nullable/unit="1"，
  pair_count 固定 numeric/nonnullable/unit=None。完整输出分别为 float64/int64。
- `expression_series` 对两端执行已有算术与必要检查；另一个端点 NA 不能使严格除零
  被跳过。只在求得有效数值后做组内共同非 NA 筛选；不同统计继续使用自己的掩码。
- 数值模块按设计固定路径实现：原值判常量和排平均秩，int/Decimal 精确减参照值，
  float 二进制缩放，两遍中心化与稳定求和，先验证有限性再裁剪至 [-1, 1]。
  不增加近常量阈值、算法回退、抽样或新的配置参数。
- 保留 Frame 当前行分组及规范键序，不把 pair_count 改写成过滤整个 Frame 后
  row_count；不会因此删除零配对组或改变同次均值样本。
- 共享的是本次输入及基础内核，不要求新建跨统计表达式缓存。计数不触发排名或
  系数计算；两种相关方法共用设计中的中心矩计算。

### S3 的高层入口与共享求值

以一次 ReduceValues 实现 correlate，复用单输入的完整轴目录和检查流程。
普通高层例子可直接对 pending frame 调用 correlate 后 compute；
只有需要重复分析同一份固定数据的例子才先 compute 输入。

C11 保留固定输入上的两层等价比较，并追加设计指定的一个 pending 见证：

1. 使用单来源、无分组、Pearson 请求；不预先 compute。
2. 用既有 monkeypatch 在目标业务读取处设置批次：首次为
   `(1,1)、(2,2)、(3,3)`，若重复读取则为 `(1,3)、(2,1)`，保持类型与完整键合法。
3. capture_source 记录实际业务读取；构造和 pending show 为零次，
   对返回 Frame compute 一次后目标来源恰好读取一次。
4. 同时断言 coefficient=1（设计容差）及 n_pairs=3；不能只比较两个固定结果，
   也不能只检查图中引用了相同来源。schema 准备不计为业务读取。

仅此一个 pending 见证，不对方法、分组、载体或 provider 参数化；不声称跨来源事务快照。

## 4. Help 与文档同步

原生注册落点为 [dsl/_result_disclosure.py](../../../marivo/analysis/dsl/_result_disclosure.py)，
类型与消费导航由 [dsl/_disclosure.py](../../../marivo/analysis/dsl/_disclosure.py)
及既有 registry 负责，不在 renderer 中维护平行方法清单。

| 公开面 | 同步内容 |
| --- | --- |
| analysis.analysis_frame.correlate | 签名、显式 method、固定两列及类型/单位、同输入/完整轴、NA/空域、惰性效果；最小常规例子；转到 reduce 的组合导航 |
| analysis.column.corr | 仅系数的未完成统计、两种 method；用 reduce 完成；需要配套结果时指向 correlate |
| analysis.column.pair_count | 独立有效配对计数、自配对非 NA 计数、常量系数不清零计数；说明 correlate 已包含该列 |
| AnalysisFrame / Column / FrameAxis 类型页及 reduce | 常规相关优先发现 correlate，组合方法保留独立目标；FrameAxis 列出新的消费者；reduce 说明新统计仍单项单列、名称不会推断配对关系 |
| docstrings 与结构化错误 | 英文目的、参数、返回值、例子、约束；已知和延迟 Duration 拒绝的 repair 正确；定位两端及实际调用入口 |
| typing / exports / pending show | 公开返回类型具体；三方法不引入新顶层导出；有序双端与方法可见，show 不准备 schema 或读取数据 |

静态验证不能只看页面数量：覆盖 canonical string、公开类方法及绑定方法的同一归属，
三条新增目标与导航独立可达，CLI/Python 内容一致；运行最小示例并检查既有预算。
新叶子的内容/长度断言留在现有披露测试内，不增加独立的 Agent 调查或基准工程。
保持 reduce 的通用说明简短，完整相关用法由对应叶子承担。

在实现完成的 S4 同步以下拥有相关事实的文档，不提前把 proposal 写成已实现：

- `docs/specs/analysis/query-result-reuse.md` 的方法表、名称/结果和规约合同；
  `operators-and-frames.md` 的统计与数值边界；设计稿状态与本计划证据。
- `docs/specs/analysis/lazy-analysis-dsl-examples.md` 的常规入口和组合例子。
- [Python API 文档](../../api/analysis.rst) 的当前数据操作说明及自动方法文档。
- [英文 analysis-workflow](../../../site/src/content/docs/docs/latest/concepts/analysis-workflow.mdx)
  与 [中文 analysis-workflow](../../../site/src/content/docs/zh-cn/docs/latest/concepts/analysis-workflow.mdx)。
  其它 latest 页面仅在原文列举范围或示例受影响时同步，不扩写全部教程。

AGENTS.md 与 packaged skills 不在本次修改授权内。普通 API 事实应由 Help 拥有；
若后续发现 skill 中有与新能力冲突的判断规则，列明实际缺口并单独取得授权，
不能据此预设重写 skill。

## 5. C01–C11 验收安排与夹具

主测试文件拟新增 `tests/analysis/materialization/test_frame_correlation.py`，
集中公共入口和设计的少量数值见证。表中“主文件”均指此文件；其余测试已存在。
这是测试归属安排，不要求每个 C 编号独立建文件或把同一组样本复制到两层。

| ID | 判定重点 | 测试落点与复用 |
| --- | --- | --- |
| C01 | 参数、精确 Frame/Session/完整轴绑定、method 必填、惰性构造/show | 主文件；复用 test_frame_structure.py、test_analysis_frame.py 的句柄/不读取断言模式，typing 用 analysis_surface_contract.py |
| C02 | 不对称 NA、自配对、对称性、不同变量对与其它统计掩码独立；表示保序 | 主文件共享输入；沿用 frame_execution 的同次规约模式，表示/指纹使用既有编码 |
| C03 | Null 类别、零配对组、完整复合轴、规范键序与 presence 删除 | 主文件少量分组样本；回归 test_frame_execution.py 的完整复合轴规范键序见证 |
| C04 | 先配对/分组再排名、平均秩及重复数值 | 主文件；设计 §8.1 的间隙/并列见证，另一个组间交错样本，使用独立期望值 |
| C05 | 空标量、空分组、n=0/1、常量、n=2 | 主文件表驱动小样本；不乘以所有类型/载体 |
| C06 | 混合数值类型/单位；已知及 unresolved→Duration 拒绝；pending/实际 schema；精度见证 | 主文件；复用 test_exact_frame_mean.py 的精确值/类型断言模式与 test_frame_effective_types.py 的实际来源类型模式 |
| C07 | 非有限/零尺度失败、严格子式不被 NA/Top-0/剪列掩盖；下推/合并拒绝 | 主文件与 test_frame_execution.py、test_frame_plan.py；用实际错误及计划资格断言，避免复制数值算法作为 oracle |
| C08 | 显式完整键 align 后相关、缺端点/零与显式 presence 筛选 | 主文件沿用 test_frame_correspondence.py 的显式对齐与缺侧样本；只做相关接入增量 |
| C09 | 三个 Help 目标、两层导航、静态合同一致、例子/预算、无新增导出 | tests/surface 下的 test_analysis_disclosure_content.py、test_analysis_help.py、test_help.py、test_exports.py，以及 tests/project/test_cli.py 的 CLI/Python 一致性参数表 |
| C10 | sum/mean/row_count 回归；普通结果保存、冷读、续算；无总体相关资格 | 已有 test_frame_execution.py、test_exact_frame_mean.py 回归；在 test_frame_storage.py 的保存/冷读机制补一组 float64/int64/NA 输出，保留 schema 和键序 |
| C11 | 两层固定输入等价、固定两列、普通 Frame 续算及一次输入求值 | 主文件复用 C02–C08 的代表样例；pending 只用 §3 的单例和现有来源追踪，不重跑整套矩阵 |

夹具与证据复用：

- [tests/conftest.py](../../../tests/conftest.py) 的 analysis_dsl_case_factory 及
  [tests/shared_fixtures.py](../../../tests/shared_fixtures.py) 的 DslCase/行数据/数据库构造，
  用于公开 Session、字面量和来源测试；不复制 datasource、semantic project 初始化。
- 对固定数值/结构用例沿用
  [test_frame_execution.py](../../../tests/analysis/materialization/test_frame_execution.py)
  的完整 typed Frame 与现有调度方式；真正重复的 setup 才提取到共享夹具，
  不导入其它测试文件的私有函数或为相关统计另建测试执行器。
- C11 复用 [capture_source](../../../tests/support/source_trace.py) 与
  [既有共享来源测试](../../../tests/analysis/numeric/test_analysis_references.py) 的计数方式；
  目标批次变化只在该测试的 monkeypatch 中设置，不修改全局夹具行为。
- C10 复用 [test_frame_storage.py](../../../tests/analysis/materialization/test_frame_storage.py)
  的冷进程入口及禁回源检查；一个包含系数 NA 和非空整数计数的结果足够验证新增列。
- 数值 oracle 使用设计手算结果、Fraction/高精度 Decimal；
  dtype、掩码、键序、计数精确比较，小规模系数绝对误差不超过 1e-12。
  两层输出相等不能替代独立正确性判定。

## 6. 检查、证据与完成条件

实施时按改变的范围执行，以下命令不是本轮通过记录。新文件在其实施阶段创建后再运行。

```sh
make test TESTS='tests/analysis/materialization/test_frame_correlation.py'
make test TESTS='tests/analysis/materialization/test_frame_execution.py tests/analysis/materialization/test_exact_frame_mean.py tests/analysis/materialization/test_frame_effective_types.py tests/analysis/graph/test_frame_plan.py'
make test TESTS='tests/analysis/materialization/test_frame_storage.py tests/analysis/materialization/test_frame_correspondence.py'
make test TESTS='tests/surface/test_analysis_disclosure_content.py tests/surface/test_analysis_help.py tests/surface/test_help.py tests/surface/test_exports.py tests/project/test_cli.py'
make typecheck TYPECHECK_TARGETS='marivo/_definition/query.py marivo/analysis tests/typing/analysis_surface_contract.py'
```

各阶段对实际修改的 Python 文件执行 `make lint-agent LINT_TARGETS='...'`；
类型检查覆盖全部实际修改模块。S5 在最终源码上运行未缩小的 `make check-agent`
（已包含默认测试及 API 文档构建），不以若干窄测试替代。
latest MDX 修改后使用站点现有 `npm --prefix site run verify:content` 与
`npm --prefix site run build` 验证内容和构建。

首版不取得来源相关下推资格，不安排全 provider/Runtime 或 release 矩阵；
只有实际修改了现有 provider 边界时才补对应 Runtime 回归并单列结果。
不为普通开发启动 release-check。

每阶段记录实际修改文件、命令/退出码、C 编号与证据位置、失败原因及修复后复验结果。
最终记录 HEAD 和未提交差异对应的源码快照；阶段证据不能被不同源码的通过结果替代。
如安排正式审查，最多两轮，未解决 P2+ 保留为阻塞，不用继续加轮次代替结论。

完成条件：

1. C01–C11 全部有可定位的判定，pending 共享求值同时有读取次数和结果证据。
2. 三个公开方法、类型、错误、Help、示例和文档一致；统计定义及执行只保留一套权威。
3. 最终源码通过 required typing/lint、check-agent 及受影响文档检查；
   未运行的 Runtime/发布资格明确列出，不推广为通过。
4. 保存结果仍是普通 Frame；未引入隐藏样本、来源恢复或额外公共抽象。

本轮交付仅为此计划；上述实施、测试、Help 同步和发布资格均未开始。
