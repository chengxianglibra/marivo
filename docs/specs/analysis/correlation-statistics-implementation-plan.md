# Correlation Statistics 实施计划

Status: complete (local acceptance)，2026-10-20；S0–S5 与 C01–C11 完成，
最终未缩小 check-agent 通过，gpt-6-astra/high 正式 R2 为 No findings。
未运行 provider Runtime 或 release 验收。实施验收时未提交；后续提交与 main 集成见第 8 节，未推送。

实施基线：`657334c78d58d3265e5d3be38b2819bd561233ed`；隔离 worktree 为
`/Users/lichengxiang/.codex/worktrees/correlation-statistics/marivo`，分支为
`codex/correlation-statistics`。主工作区的既有未提交内容不进入本次候选。
设计版本为同一基线中的 `correlation-statistics-design.md`。
命令原始输出保存在 `/tmp/marivo-correlation-evidence/`。

团队责任：expression_types 负责 S1 表达、类型及实际类型消费者；runtime_kernel
负责 S2 数值、执行及优化消费者；acceptance_tests 负责 C01–C08/C10–C11；
主 agent 负责 S3、S4、集成与证据记录。独立正式审查使用 gpt-6-astra/high，
最多两轮；未解决 P2+ 阻塞完成。实施阶段不提交；后续提交授权见第 8 节，不推送。

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

阶段状态与最终证据如下。S5 已完成本次本地验收。

| 阶段 | 责任与主要文件 | 依赖 | 退出条件 |
| --- | --- | --- | --- |
| S0 | 核对设计、基线及共享 Statistic 消费者 | 本计划 | 确认工作区范围、验收归属和实际修改列表；不重新打开已确定的公开签名 |
| S1 | 表达/类型：query.py、column.py、frame_expression.py、frame_plan.py、query_program.py | S0 | 双端捕获、类型准备、固定输出 schema 闭合；C01/C02/C06 的构造与结构部分通过，修改模块类型检查通过 |
| S2 | 执行/数值：frame_values、frame_correlation、实际类型与优化消费者 | S1 | C02–C08 的底层统计见证通过；零配对组和独立掩码保持；旧路径不会误收新统计 |
| S3 | 高层入口：frame.py；pending show、共享求值与普通 Frame 续算 | S2 | C01、C11 完成，C10 保存恢复增量通过；两层没有第二套算法或独立读取 |
| S4 | 公开披露：docstrings、Help 描述、类型/导出约束、文档 | S3 | C09 通过；常规与组合入口的例子实际可执行，英中和 API 文档一致 |
| S5 | 集成验收与收尾 | S1–S4 | C01–C11 全部有结论；最终源码及未缩小的 check-agent 通过；无未解决 P2+，证据分层明确 |

| 阶段 | 当前状态与证据（`/tmp/marivo-correlation-evidence/` 下） |
| --- | --- |
| S0 | 完成；基线/隔离范围见开头；逐一核对 Statistic/ReduceValues、旧 ResultProgram、优化与来源融合消费者 |
| S1 | 完成；`s1-final-typecheck.log`：6 源文件 typing 通过；`s1-final-lint.log`：格式、lint 与导入合同通过；双端类型由 FrameProgram 分别承载，实际类型仍经 effective_frame 重建 |
| S2 | 完成；`s2-regressions.log`：66 项通过；`s2-correlation-smoke.log`：28 项通过；`s2-typing-final.log`、`s2-lint-final.log` 通过；新增单一 frame_correlation 内核及来源/旧路线拒绝 |
| S3 | 完成；correlate 固定两项 ReduceValues，pending show 披露有序端点；C11 与普通冷恢复专项通过 |
| S4 | 完成；`disclosure-tests.log`：127 项通过，含三叶子执行示例/类/绑定方法/CLI 一致性；`disclosure-lint.log` 通过；最终 `site-content-final.log`：381 required files；`site-build-final.log`：359 pages、44 latest pages/343 targets 通过；`help-context-budget.log`：既有任务预算 1 项通过 |
| S5 | 完成；gpt-6-astra/high 正式 R1 的 1 项 P2 已修复；正式 R2 为 No findings，无遗留 P2+；`check-agent-final.log`：未缩小 gate 退出 0，4810 测试通过、304 文件 typing、lint/导入及 API 文档构建通过 |

已分类失败：S1 首次 typing 的局部变量注解和并行编辑中的旧 `.value` 分支已修复；
新增规划测试首次查找了不适用的本地 stage，修正见证后 66 项回归通过。
最初站点构建沿共享 venv 的 editable 安装导入主工作区，触发其它任务的文档失败；
保留 `site-build.log`，显式设置本 worktree 的 PYTHONPATH 后完整构建通过，
未修改主工作区。验收夹具/断言修正及最终结果在 S5 汇总。

正式审查 R1：`review-r1.md`，独立专项 33 项通过，额外核验低 Decimal context、
宽整数差值及 200 组样本的对称/重排序。发现 P2：声明为 float64 的合法 Decimal
除法表达式未在配对输入边界完成 float64 转换，correlate/corr/pair_count 均会拒绝；
复现及原始输出为 `review-r1-probe.py` / `review-r1-probe.log`。
该问题交给未实现原内核的 expression_types 修复，保留精确 int/Decimal 路径；
该阻塞已由修复、回归和正式 R2 解除。

正式 R2：`review-r2.md`，No findings；原始复现和补充数值/溢出/端点上下文探针通过，
独立专项 34 项通过。修复后生产差异 SHA256 为
`af6ecb9813367aa5d85f67753e8f85f315d60fb10932353a6e877e4a5f8d2410`，
新内核 SHA256 为 `bf923310190efd46b3ed1e6f8a0239be30eed6c3583b6cf03c618f55fc74b43d`。
正式审查已完成两轮，不再增加轮次。

首轮未缩小 gate：`check-agent-r1.log`，lint/导入及 304 文件 typing 通过；
默认测试在 6 失败/4629 通过时停止。这 6 项均为原测试断言 Frame 不存在 correlate，
包括 imports、deviation 构造和 4 个 pandas views 参数；与新合同直接冲突。
仅更新这些负向表面断言，并继续断言 Session/Artifact 不提供该入口；
`stale-contract-tests.log` 的 13 项复验通过。相关慢 typing 探测仍拒绝 Frame 作为
Column 端点，而不依赖旧的“方法不存在”错误。

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

以下为计划的命令模板；实际执行范围、退出码及结果由阶段表和第 7 节记录。

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

## 7. 实际验收记录

下表记录最终候选的专项判定；S5 的完整 gate 和正式 R2 退出独立拥有最终完成结论。
`test_frame_correlation.py` 位于 `tests/analysis/materialization/`。

| ID | 专项判定与定位 |
| --- | --- |
| C01 | 通过：required_method_binding、cross_session_pair_binding、pending_correlate；签名、错误参数、双端/完整轴绑定及构造/show 零读取 |
| C02 | 通过：group_domain_masks、symmetry_keeps_ordered_capture；NA/其它统计独立、自配对与有序指纹 |
| C03 | 通过：group_domain_masks_composite_keys；Null、复合键、规范排序、零配对组及 presence 删除 |
| C04 | 通过：pair_before_rank、spearman_average_ties_and_group_local_ranks；配对后分组内平均秩，独立手算 oracle |
| C05 | 通过：degenerate_and_empty_scalar；空标量/空分组、0/1/2 配对及常量端 |
| C06 | 通过：numeric_precision_witnesses、known_duration、unresolved_source_duration、effective_source_float_types；大整数/Decimal/二进制极值及相邻值；R1 新增 float64_decimal_division_endpoints_match_explicit_select |
| C07 | 通过：strict_subexpression、numeric_intermediate_failures、pair_count_does_not_compute_coefficients_or_ranks；NA/Top-0/剪列/后置筛选、有限性/零尺度及结构化字段；test_frame_plan.py 的 paired_reductions_stay_local_and_keep_both_endpoints 拒绝融合/合并 |
| C08 | 通过：complete_key_alignment_zero_and_explicit_presence；完整复合键对齐、缺侧、有效零、显式 presence 新输入域 |
| C09 | 通过：disclosure-tests.log 127 项，新增 test_correlation_help_contract_and_executable_examples；三个目标的 string/class/bound 归属、导航、预算、执行例子、CLI/Python 一致及既有导出快照 |
| C10 | 通过：s2-regressions.log 66 项包含旧 sum/mean/row_count；test_frame_storage.py::test_correlation_is_ordinary_typed_frame_after_cold_recovery 验证 float64/int64/NA、禁回源、完整键序、续算及再次保存 |
| C11 | 通过：group_domain_masks 等固定输入两层比较及独立 oracle；pending_correlate_shares_one_mutable_source_batch 同时断言业务读取恰好 1 次、返回 3 行输入、coefficient≈1/n_pairs=3、无 Run；返回值可继续普通 Frame 操作 |

专项日志：`acceptance-focused-final.log` 为修复前 32 项通过，
`r1-fix-affected-tests.log` 为修复后 34 项通过（包括规划拒绝资格见证）；
`r1-fix-red.log` 保留 R1 P2 的先失败见证。`acceptance-failure-note.md` 为夹具与断言
开发失败的回顾分类，明确区别于原始输出。另一次探索性检查把普通 runtime 测试文件
加入 mypy，产生无效输入探测/联合类型收窄错误；保留 `r1-fix-typing.log`，
未扩写无关测试。仓库要求的生产类型检查通过于 `r1-fix-typing-production.log`，
最终未缩小 gate 仍覆盖全部 `marivo` 与 `tests/typing`。

Runtime/provider 资格及 release-check 未运行；本次未改 provider 实现或授予来源相关下推资格。

### 最终冻结与检查

生产代码和测试在最终 gate 开始前冻结。最终执行：
`PYTHONPATH=/Users/lichengxiang/.codex/worktrees/correlation-statistics/marivo make check-agent`，
不缩小 TESTS、TYPECHECK_TARGETS 或 LINT_TARGETS；退出 0。
`check-agent-final.log` 保留 4810 passed / 711 warnings 及 API 文档成功输出。
`correlation-typing-probe.log` 的受影响慢 typing 检查 1 项通过，
`help-context-budget.log` 的既有任务上下文预算 1 项通过；两者退出均为 0。

最终 gate 期间仅补充英中 statistics 指南的相关能力边界说明；随后最终站点内容检查与
完整构建退出均为 0。`final-candidate-manifest.json` 的其余 34 个冻结文件核对无变化，
生产差异和新内核仍与正式 R2 的上述 SHA256 一致。最终交付文件清单位于
`final-delivery-manifest.json`（包含这两份指南，排除本证据台账以避免自引用）；
实施验收时 HEAD 为 `657334c78d58d3265e5d3be38b2819bd561233ed`，实现为 worktree 的未提交差异。
收尾仅将设计文档状态更新为 locally validated；生产代码和测试未再修改。
最终 36 文件清单 SHA256 为
`a7c3719d4e49acc40d6fdac3df6025195fbc4f2fa99bd2f428d984a92194362c`。

实际新增文件为 `marivo/analysis/methods/frame_correlation.py` 和
`tests/analysis/materialization/test_frame_correlation.py`；其余实际文件与各自 SHA256
由最终清单定位。AGENTS.md 与两个 packaged skills 均未编辑。

## 8. 提交与 main 集成

2026-10-20，用户授权按 commit-attribution skill 提交本次实现和修复，合并回 main，
保留主工作区无关改动，不推送。

- 实施提交：`df99e839b25fe79dbdac15e9c5706f1319b44b2b`，
  `feat: add current-row correlation statistics`；37 个已审查文件，含开发、验收与
  gpt-6-astra 审查的实质贡献归因，正常提交 hooks 全部通过。
- main 集成基线：`b51fe999a1afdbcf7ec3cd1a4d6f16a377e0f126`（谓词统一）。
  在隔离 worktree 的 `codex/correlation-main-integration` 分支自动合并，
  没有冲突或手工源码修复；保留 main 的普通布尔 Column/原生条件合同。
- 集成检查：相关统计、完整 Frame 规约/存储/对齐、谓词统一及公开 Help/CLI 共
  403 项通过；未缩小 lint/import 检查和 305 文件 typing 通过。
  原始日志为 `/tmp/marivo-correlation-commit/integration-{tests,lint,typecheck}.log`。
- 集成文档：381 required files 内容检查通过，完整站点构建 359 pages、
  44 latest pages / 343 targets 通过，包含 API 文档构建。
  日志为 `/tmp/marivo-correlation-commit/integration-site-{content,build}.log`。
- 本次集成没有重复正式审查；第 7 节的完整 gate 属于其原始冻结候选，
  本节明确记录与当前 main 的组合检查，不扩展为 provider/Runtime 或发布资格。
- 主工作区 README 的其它改动和五个未跟踪文档不进入提交；原始内容、索引和
  差异已备份于 `/tmp/marivo-correlation-commit/main-before*`，供合并后逐项核对。
