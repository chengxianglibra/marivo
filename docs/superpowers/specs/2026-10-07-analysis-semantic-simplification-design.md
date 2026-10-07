# Analysis DSL 与语义层简化方案

Date: 2026-10-07

Status: O1 and O2a implemented and validated within the stated scope. O2b and O3–O5 remain proposed.

Baseline: `panda@ccbed62962e21beef5778c738037e62d912ec8fb`.

优先收敛重复图校验、物理实现声明和语义规范化，再处理持久化依赖与表达式书写约束。
目标是减少同一事实的重复推导、重复表示和重复检查，同时保持结果正确性与清晰的失败边界。
前三项先按等价内部整理实施；改变支持范围、恢复协议或作者语法的部分分别交付。

O1、O2a 已按本文的等价内部整理边界实施，并更新对应私有构造、编译交接与消费者规范。
其他工作包仍是优化提案；本次不授予新的后端、类型或方法组合资格，也不表示
R10、安装包、真实 Agent 或发布验收已经完成。`AGENTS.md` 与 packaged skills 保持原样。

## 1. 范围与设计原则

优化覆盖 Semantic 编译状态、Analysis 定义图、方法选择、lowering 和固定 Artifact 读取。
保留现有 Semantic、Analysis、Datasource、Runtime、Store 分层，优先复用已有类型和 owner。
不引入通用证明引擎、第二套 registry、跨进程编译缓存或新的公共“已验证”令牌。

每个检查应回答三个问题：它验证哪个事实，该事实在哪个边界可能变化，失败由谁报告。
同一不可变定义的事实可以复用；当前源数据、当前物理绑定和本次执行完成情况必须由
相应执行边界验证。删除一项检查前，应能指出接替它的唯一责任位置或说明该检查没有独立责任。

以下规范继续拥有当前契约；相应工作包实施时，只修改受影响章节：

- [Python Analysis design](../../specs/analysis/python-analysis-design.md)：图、方法选择和执行交接。
- [Operators and frames](../../specs/analysis/operators-and-frames.md)：算子语义和保留状态。
- [Session state and runtime](../../specs/analysis/session-state-and-runtime.md)：运行、发布与恢复。
- [Semantic overview](../../specs/semantic/overview.md)、[Semantic object model](../../specs/semantic/semantic-object-model.md)：业务定义和表达式契约。
- [Loading and validation](../../specs/semantic/loading-validation-introspection.md)：加载、编译与 readiness。
- [Public surface](../../specs/agent-friendly-public-surface.md)、[AGENTS.md](../../../AGENTS.md)：公共披露与仓库规则。

## 2. 当前证据与优先级

以下观察绑定上述基线。调用次数来自纯内存构图探针，声明数量来自当前 `REGISTRY`；
二者均不证明端到端加速或真实后端资格。未测量的时间、内存和代码削减比例不预填估计值。

| 工作包 | 当前问题与证据 | 优先级 | 首次交付边界 |
| --- | --- | --- | --- |
| O1 图校验 | `method_node()` 验证输入拓扑、节点构造再次校验、随后再验证完整拓扑；`lower()` 重新规划并比较 | 最高 | 复用静态校验结果，保持公开拒绝边界 |
| O2 实现声明 | 84 个方法注册、11,470 条物理实现声明；部分准入按 `r93.c…` 实现 ID 前缀判断 | 高 | 支持集合、路线和持久化身份保持等价 |
| O3 语义编译 | `normalize_target_metric()` 每次进入 Metric forest lowering 与规范化 | 高 | 在同一编译状态内按需复用成功的静态契约 |
| O4 持久化 | `validate_descriptor()` 恢复图并调用 `descriptor_plan()`，后者重新选择原生产实现 | 中 | 先消除一次操作内的重复验证，再单独调整协议 |
| O5 作者语法 | 表达式体只允许单个 return；验证、列提取和编译分别处理 AST | 中 | 先统一解析，局部表达式绑定另作语法增量 |

O1 的基线探针连续添加 `PartsTransport("view", ...)`，记录 `_validate_method` 调用次数：

| 新增节点数 | 方法校验调用次数 |
| ---: | ---: |
| 10 | 110 |
| 20 | 420 |
| 40 | 1,640 |

该构图路径出现二次增长。它不是整个编译器的复杂度结论；签名大小、保留 parts、
输出布局和方法自身推导可能另有成本，优化后需分别测量。

主要代码入口：

- O1：[graph.py](../../../marivo/analysis/core/graph.py)、[graph_plan.py](../../../marivo/analysis/compiler/graph_plan.py)、[graph_lowering.py](../../../marivo/analysis/compiler/graph_lowering.py)。
- O2：[builtin.py](../../../marivo/analysis/methods/builtin.py)、[registry.py](../../../marivo/analysis/methods/registry.py)、[physical.py](../../../marivo/analysis/methods/physical.py)。
- O3：[compiled state](../../../marivo/semantic/_compiled_state.py)、[metric graph lowering](../../../marivo/semantic/metric_graph_lowering.py)、[graph observation](../../../marivo/analysis/materialization/graph_observation.py)。
- O4：[graph protocol](../../../marivo/analysis/materialization/graph_protocol.py)、[graph snapshot](../../../marivo/analysis/materialization/graph_snapshot.py)、[graph storage](../../../marivo/analysis/materialization/graph_storage.py)。
- O5：[validator](../../../marivo/semantic/validator.py)、[expression binding](../../../marivo/semantic/_expression_binding.py)。

## 3. 保留的语义边界

| 事实 | 责任位置 | 优化后的要求 |
| --- | --- | --- |
| Entity、Metric、单位、业务时间和依赖含义 | Semantic 编译状态 | 同一不可变定义复用；重新加载后使用新状态 |
| DAG 身份、角色、派生签名与归属 | Analysis 构造与编译入口 | 新节点局部检查；外部输入和执行入口完整检查 |
| 后端、字段类型、schema、时区和实际绑定 | Datasource 与执行准备 | 按本次绑定验证，静态缓存不替代物理事实 |
| 唯一键、配对、覆盖、数值有效性 | 对应执行消费者 | 绑定实际输入与检查时机；不复用旧源读取证明新读取 |
| Cell 状态、聚合分量和 retained parts | 方法语义与结果消费者 | 保留 Unknown、Null、Undefined、缺失坐标的区别 |
| receipt、文件完整性和发布状态 | Store 读取与发布边界 | 读取当前受约束字节，完整性校验不被元数据缓存跳过 |
| 取消、截止时间和资源释放 | Runtime 与 provider | 完成条件不因静态简化而放宽 |

跨 Session/owner 输入、错误粒度与比较对齐、Decimal/Duration 精度、时间解释、
完整性不足和原始聚合状态不足仍必须正确拒绝或返回既有状态。声明、静态资格、
当前执行证据仍是不同事实，不能互相替代。

## 4. O1 收敛图校验与编译遍历

### 目标结构

构造新节点时从直接输入签名推导一次结果，检查新增参数、直接输入角色、类型和归属。
由既有内部构造路径产生且未跨边界的节点，不为每次追加节点重新遍历全部祖先。

编译入口对捕获的定义闭包完整检查一次，得到依赖顺序、节点索引、派生结果和必要摘要。
计划生成、classification、lowering 和同一次调用内的静态消费者复用这些结果。
lowering 继续校验实际绑定与计划的对应关系，停止通过重新运行整个 planner 验证内部交接。

完整检查至少覆盖重复 identity、环、外部伪造节点、错误签名、跨 owner 输入、被修改的
计划和持久化解码。复用只能绑定同一个捕获对象及其 registry 解释，不能仅凭业务
fingerprint 或调用方传入的布尔值信任对象。`frozen=True` 本身也不构成跨边界证明。

### 范围与出口

- 保持节点身份与定义 fingerprint 的区别：共享同一节点只执行一次，等定义独立节点不合并。
- 保持公开非法输入的拒绝时机，尤其是业务读取和 Run 分配之前的拒绝。内部伪造对象
  测试若改在编译入口拒绝，应明确修改该私有边界契约，不删除反例。
- 反序列化、跨调用输入和新的物理绑定仍经过各自入口检查；不缓存跨 Run 的源数据检查结果。
- 上述固定大小签名的链式探针，其构造期方法校验调用次数随节点数线性增长；共享 DAG
  的完整入口遍历按节点与边访问，不按展开后的路径重复计算。
- 现有 source/fixed 路线、结果、parts、错误种类和持久化摘要保持等价；摘要需要改变时
  移到 O4 协议变更中处理。

不以消除所有检查为目标，也不把所有静态错误推迟到 `execute()`。

### O1 实施记录（2026-10-07）

- `MethodNode.derivation` 由指定 registry 在构造时产生；新增节点只推导和局部校验一次。
  深层 identity 冲突、伪造祖先及 retained 证据错误在完整编译入口拒绝，反例继续保留。
- 一次编译捕获执行顺序、完整 retained 定义、索引、derivation 与原 fingerprint；
  classification、实现选择与 lowering 静态查询复用它。retained 定义不生成执行阶段。
  独立恢复的 retained 历史定义允许等定义的同 identity 对象，逐对象检查并保护交接；
  执行依赖仍严格要求一个 identity 对应一个对象。
- 计划交接绑定原计划对象与 registry，检查节点、阶段、checks、物理要求及所用注册的
  精确类型内容快照；对象复制、嵌套修改和 registry 替换不能借用该交接。
  审查修复另确认每个已使用方法仍 lookup 到原 registration，防止其他注册修改 key 后
  抢先匹配。table/Parquet 反例均先复现旧交接被接受，再验证修复后拒绝。
- 删除构造期输入/结果全图校验、planner 的第二次拓扑校验和重复 derivation、lowering
  的重新规划及静态查询全图校验。同次执行复用一次 snapshot 写出，独立解码仍推导
  并比较保存的 derivation，完整 retained endpoint 匹配仍由图 owner 校验。
  执行入口仍先做 snapshot 结构与预算检查，保持环、深度和节点预算的原错误边界。
- 快照比较仅在当前调用内按对象共享复用值哈希；没有全局编译缓存。计划绑定使用弱引用，
  捕获对象随计划释放。业务数据、物理绑定、receipt、取消与资源检查保留原责任。
- 两个确定身份的 source/fixed 样例对照 `panda@d7b0526a75`：definition fingerprint、
  plan digest 与 snapshot 字节哈希完全一致。未修改 Store 格式、版本或摘要规则。

固定大小 `PartsTransport("view", ...)` 链的独立本地探针结果如下。
时间为 7 次重复的中位数；内存另用 `tracemalloc` 单次测量，不与计时混跑。
这些测量取自补充 registry lookup 一致性检查之前，未重新测量该检查的增量开销。
基线从提交归档加载，优化版从当前工作区加载；测试 oracle 不依赖这些临时探针文件。

| 节点数 | 构造校验：前 → 后 | 规划校验：前 → 后 | 构造时间 ms：前 → 后 | 规划时间 ms：前 → 后 |
| ---: | ---: | ---: | ---: | ---: |
| 10 | 110 → 10 | 20 → 10 | 6.833 → 2.160 | 8.099 → 28.265 |
| 20 | 420 → 20 | 40 → 20 | 19.604 → 2.714 | 16.250 → 36.914 |
| 40 | 1,640 → 40 | 80 → 40 | 72.823 → 3.747 | 33.956 → 54.970 |

| 节点数 | 构造峰值 bytes：前 → 后 | 计划保留 bytes：前 → 后 | 规划峰值 bytes：前 → 后 |
| ---: | ---: | ---: | ---: |
| 10 | 36,600 → 14,917 | 11,369 → 26,049 | 18,129 → 1,124,134 |
| 20 | 114,286 → 22,506 | 23,539 → 50,055 | 30,811 → 1,141,882 |
| 40 | 204,369 → 41,034 | 36,263 → 72,195 | 44,527 → 1,166,242 |

构造期校验已从二次增长降为线性；完整入口每个方法验证一次，lowering 的回归测试
要求零次重新验证、推导和选择。防篡改内容快照增加规划时间、保留状态和临时内存；
40 节点的构造加规划从 106.779 ms 降为 58.717 ms，10/20 节点的总时间并未改善。
这些仅是纯内存样例，不证明完整执行加速或 provider 资格。

验收结果：

- R3.3/R3.4 首轮基线为 127 passed；实施后先运行这些测试，再覆盖 graph execution、
  snapshot 与 publication。新增线性计数、共享 DAG、深层冲突、伪造祖先、retained
  别名及交接篡改反例，保留现有跨 owner、环、错误 derivation 和 endpoint 错配反例。
- 最终 `make check-agent` 通过：729 个文件的格式/lint、import contracts、333 个源码文件的
  typing、默认测试 4,731 passed / 1 skipped（49.30 s），以及 API 文档构建。
  审查修复后的 R3.3/R3.4 定向回归为 160 passed（8.20 s）。
- 定向 `make runtime-test TESTS='tests/test_analysis_graph_publication_r44.py'` 为
  17 passed（46.70 s），包括真实 source/fixed、parts、状态、共享阶段和冷进程恢复。
  另以 `.venv/bin/pytest -m runtime -n 0` 运行公开 comparison 的 BIGINT、Decimal、Duration
  source/fixed 用例及 additive attribution：4 passed（26.81 s）。
  上述 Runtime 结果来自初次实施；registry lookup 交接修复后未重复运行。
- 结构环、节点/深度预算、非法图与交接修改在业务读取及 Run 分配之前拒绝。
  这些证据没有扩大 provider 支持范围；未运行完整 Runtime、安装包或发布验收。
- 相对实施基线 `panda@d7b0526a75`，生产源码新增 402 行、删除 118 行，净增 284 行；
  测试新增 266 行、删除 4 行，净增 262 行。删除的是上述重复路径，新增内容主要是
  捕获与精确交接保护，并非净代码削减。`AGENTS.md` 与 packaged skills 无变更。

## 5. O2 将能力限制归还给实现消费者

### O2a 等价整理

保留唯一 `MethodRegistry` 和精确的输入类型、域、shape、route 判断。相同消费者拥有的
重复声明用小型静态表和现有专用函数表达；方法语义继续拥有 required checks、parts、
精度和结果类型，消费者拥有可执行参数范围。

删除依据 `implementation_id.startswith("r93.c…")` 决定能力的逻辑。实现 ID 保留为
稳定身份或溯源信息，行为限制由明确的消费者规则决定。第一步保持现有 ID、版本和
exact-key 选择结果，避免把内部整理变成已存 Artifact 的身份变更。

只提取真实重复的能力维度，不建设可配置规则语言或通用插件调度器。选择仍得到唯一
实现；不得因为一条路线失败而自动重试另一条路线。

### O2b 支持范围简化

在 O2a 完成后，逐项检查仅支持某个路径长度、类型组合或完全一致 shape 的限制：
它来自算法、provider 能力、语义要求，还是仅来自历史验收样例。
每次只扩大一个有明确消费者依据的范围，并补充对应边界与实际执行证据。

全图相同 shape、禁止 mixed source/fixed 等现有规则不随声明整理自动放开。若要调整，
应先给出具体合法操作、输入绑定和执行责任，再修改 owning spec。

### 出口

O2a 对基线声明集比较方法、输入类型与域、shape、route、实现 ID/版本、检查、parts、
精度和资源契约；同时覆盖 Decimal 参数族、可变输入个数、特殊参数限制和相邻拒绝案例。
只比较 11,470 条基础声明不足以覆盖动态 specialization。

通过条件为支持/拒绝集合及选择结果等价，且生产逻辑不再根据验收阶段前缀分支。
O2b 的新增资格单独记录，不反向改写历史验收结论，也不机械展开所有维度的笛卡尔积。

### O2a 实施记录（2026-10-07）

- 实施基线为 `panda@440dabc14f`，包含 O1 与按行为组织的测试目录；未修改其既有成果。
  唯一 `MethodRegistry`、声明顺序与去重规则保留。84 个方法、11,470 条基础声明的
  有序 key、资格状态、实现 ID/版本、consumer/evidence、checks、parts、精度及资源
  摘要与基线一致。测试内冻结摘要不依赖临时资格证据或源归档。
- `Implementation.numeric_specialization` 明确区分 `consumer` 与 `exact`；312 条
  exact 声明的位置与原限制一致，线性组合的输入个数扩展仍先独立处理。复制声明时
  显式设置最终消费者策略，不通过实现 ID 或证据路径推断能力。
- 合并 PostgreSQL/MySQL/Trino/ClickHouse 的重复 native 输入声明与两次相同的准入过滤，
  用小型顺序表保留原 upstream key 溯源文本。native distribution 的后端表由声明和
  参数消费者共用，没有第二套 registry、配置语言、通配 qualification 或路线重试。
- 删除 9 处实现 ID 前缀驱动的生产分支。42 条 prepared numeric 声明的本地检查归属
  由 lowering 与执行准备共享判定；123 条 Subject image 和 13 条 native distribution
  声明由明确的 key、consumer 与 parts 判定。SQLite funnel axes 和 Anchor observation
  参数限制归各自物理消费者所有；真实声明匹配、原错误文本和拒绝边界保留。
- 动态对照包含 376,339 个按现有模板产生的探针，覆盖 Decimal precision/scale、四种
  Duration 单位、混合类型、输入个数 1/2/3/16/17/64/65、域、时间、后端、table kind
  与路线邻近变化。预期由基线代码冻结，比较 specialization 的完整旧字段、匹配结果
  与异常，不只比较数量。另覆盖真实 registry 的特殊类型选择身份、邻近拒绝、ID 与
  能力解耦、伪造实现拒绝、SQLite 参数边界及新策略的交接篡改反例。
- 新策略进入 O1 的精确内容交接校验；不进入持久化 qualification key、Store 编码、
  implementation version 或 plan digest。现有 source/fixed fingerprint、plan digest
  和 snapshot 字节基线继续通过。mixed source/fixed、异构 shape、缺失 checks/parts
  及非法图的既有拒绝责任不变。

验收结果：

- 首轮受影响默认测试为 373 passed（11.25 s）；补齐 shape/route 探针与实际选择反例后，
  专项测试为 138 passed（9.73 s）。新增测试模块与受影响源码的 typing 为 38 个文件通过。
- `make check-agent` 通过：764 个文件的格式/lint、import contracts、335 个源码文件的
  typing、默认测试 4,731 passed / 1 skipped（50.04 s），以及 API 文档构建。
  后补的完整注册方法集合/顺序断言单独复核为 1 passed（0.61 s）。
- 定向 Runtime 首轮为 10 passed（505.06 s）：DuckDB/SQLite prepared sum/mean
  attribution、SQLite Anchor observation overlap、SQLite 直接/联合 axes 与 DATE
  snapshot/validity funnel，以及 table/Parquet funnel 的独立进程恢复。保留现有
  数值、Cell/parts、来源隔离、损坏/输入变化与资源断言。最终 admission gate 的
  SQLite Anchor observation 与直接 int64 funnel 另复核为 2 passed（59.73 s）。
- 相对实施基线，生产源码新增 359 行、删除 203 行，净增 156 行；新增内容主要是明确
  的策略和消费者规则，并非净代码削减。删除的是上述前缀分支与重复 native 声明路径。
  不承诺统一执行加速比例，不扩大后端/类型/方法组合资格。
- 外部 provider Runtime、完整 Runtime、安装包、真实 Agent 与发布验收未运行。
  `AGENTS.md`、packaged skills、公共导出和 Help 未修改；O2b、O3–O5 未实施。

## 6. O3 复用已编译的语义契约

在现有 `CompiledSemanticState` 所属生命周期内，按需复用已成功产生的
`TargetMetricContract`、规范化 Metric forest 和静态依赖事实。首次请求才处理所需
依赖闭包，避免在 `ms.load()` 时强制编译全部未使用对象或提前拒绝仍可加载的定义。

缓存由内部编译上下文拥有，不暴露为公共能力；不修改对外不可变状态。实施前核对
registry、sidecar 和嵌套定义的真实不可变性，不能仅根据 dataclass 外壳判断安全。
同一加载状态必须保持确定解释；改变定义通过新加载状态生效。

缓存标识包含实际语义输入、依赖版本和解释规则版本。对 Runtime Metric 输入保留有序
根与重复位置，以及适用的 presentation 信息；不能只用 Metric 名称、相同显示标签或
一个与依赖无关的字符串作为键。新加载、不同 project/编译状态相互隔离。

仅保存成功的静态结果。不缓存连接、物理 schema、行数据、readiness 的外部完整性事实
或失败异常对象。未知物理类型仍保持待绑定状态；命中缓存不能跳过本次物理类型校验。
源表达式的构建预算和输入合法性要求仍由适用入口负责。

出口包括：同一编译状态中重复消费同一 Metric 不再重复 forest lowering；依赖变化、
新加载、不同 sidecar 和不同 Runtime Metric 顺序不会错误命中；缓存命中与首次路径
给出相同语义结果和公开失败边界。记录重复调用收益以及保留对象的内存成本。

## 7. O4 缩小固定结果对生产计划的依赖

### O4a 一次读取内复用验证结果

先保持 Store 格式、摘要、版本和恢复规则不变。在一次读取或续算操作内传递已经验证的
descriptor、恢复图和计划，避免不同 helper 再次解码、恢复和规划相同元数据。
适用位置包括固定签名、结果读取和 Materialized 对象构造。

复用元数据解释不代表磁盘字节仍未变化。文件读取继续验证对应 receipt 与实际读入内容；
同一次操作中的复用必须绑定同一已验证输入。后续独立调用、文件被替换和损坏恢复仍按
当前契约处理，不能靠路径相同或曾经读成功绕过完整性检查。

### O4b 调整持久化契约

进一步把三项责任明确分开：

| 责任 | 所需信息 |
| --- | --- |
| 读取固定结果 | 发布身份、receipt、实际 schema、语义签名、行与 Cell 契约 |
| 执行新的固定续算 | 对应 retained parts、状态版本、输入身份及新方法的能力 |
| 解释历史来源 | 生产图、原实现身份、历史 lineage 与执行证据 |

目标是固定结果的读取不再以“当前 registry 还能重新选择原生产实现”为前提。
新的续算仍检查自身方法、所需状态和输入绑定；保留的历史契约必须有明确的解码与验证
语义，不能把从生产 planner 去掉的检查原样搬到第二套 planner。

O4b 实施前必须确定最小冻结契约如何支撑现有 continuation、错误、Findings 与证据读取，
以及哪些生产图字段仍被这些消费者需要。尚未完成该清单之前不删除完整 DAG 或来源信息。

这是独立协议调整。具体版本、摘要变化和旧状态处置在其实施设计中确定；本提案不授权
迁移或删除旧文件，也不默认增加双读兼容路径。不兼容状态需有明确版本错误和修复路径。

出口为：已支持格式的固定读取不访问源、不重新选择生产路线；新续算保留所需状态检查；
跨进程冷恢复、损坏 receipt/parts、错误版本和输入替换仍正确处理。O4a 可以独立完成，
不以 O4b 完成为前提。

## 8. O5 统一表达式解析并放宽局部书写

### O5a 等价解析整理

在现有 expression sidecar/编译流程中复用同一函数的解析结果，为验证、依赖提取、
列访问分析和 body identity 提供输入。保留各 owner 的职责，避免将 validator 扩展为
执行调度器。既有合法函数的身份、错误和依赖集合必须保持等价。

### O5b 有限局部表达式绑定

在 O5a 后，允许表达式函数使用顺序局部绑定及一个最终 return。示意函数体如下；
它是拟议语法，目前不满足单返回表达式约束：

```python
def amount(orders):
    gross = orders["price"] * orders["quantity"]
    discount = orders["discount"].fill_null(0)
    return gross - discount
```

增量限于普通局部名字、之前已定义的局部值、现有合法字段绑定与表达式运算。
拒绝局部重赋值、属性或容器写入、覆盖参数、使用未定义名字及控制流。
不新增任意 helper、动态 I/O、SQL 执行或外部表引用能力；既有表达式调用范围不扩大。
Event 等独立受限语法不自动获得相同扩展。

局部值按原顺序构建并保留共享，不能用简单文本替换导致同一表达式被重复求值。
依赖和列访问从规范化绑定结构提取。局部名字变更的 fingerprint 规则、已有单 return
函数的身份保持方式以及不支持表达式的错误位置，需要与该语法一起定义和验证。

该项主要减少作者重复表达式与深层链式调用，初期不承诺减少 validator 行数。
若实现需要扩展成通用 Python 静态解释器，应缩小语法范围。实施时同步
Semantic object model、原生 Help 约束、正反例、typing 和最新中英文文档；
packaged skills 仅在确需修改且取得仓库要求的明确授权后编辑。

## 9. 交付顺序与验收

推荐主顺序为 `O1 → O2a → O3 → O4a → O5a`。每项单独形成可审查、可回退的变更。
O2b、O4b、O5b 是分别调整能力、恢复协议和作者语法的后续工作，不捆绑进首轮优化。
它们需要相应契约决策落定后实施，不以本文存在代替实施授权。

每包提交简短的变更说明、受影响检查责任、实际删除/合并的重复逻辑和验收结果。
不建立新的大型阶段台账，不将旧资格记录复制成另一份运行时清单。

| 维度 | 验收方式 |
| --- | --- |
| 正确性 | 独立结果 oracle、Cell/parts/身份、错误种类和公开拒绝时机对照 |
| 构图成本 | 链式与共享 DAG 的节点数、方法推导/校验次数；时间和内存另测 |
| 能力等价 | O2a 基础声明、动态 specialization、参数边界与邻近拒绝案例 |
| 语义复用 | 同状态重复调用次数、新状态失效、依赖与顺序隔离 |
| 固定结果 | 元数据恢复/规划次数、receipt 完整性、固定续算和冷恢复 |
| 维护成本 | 删除的重复分支、减少的事实 owner、新增辅助类型和净代码变化 |

调用次数用于解释成本，功能反例用于证明边界；不把内部调用次数测试当作唯一正确性
证据。首轮不承诺统一加速比例。若仅增加缓存、标志位和 wrapper，却没有删除重复路径
或取得可测收益，应停止该实现并重新缩小方案。

测试遵循现有仓库入口：先用 `make test TESTS='...'` 跑受影响的最小范围，再按共享
行为影响运行 `make check-agent`。O1 首查 `tests/test_analysis_graph_r33.py` 与
`tests/test_analysis_lowering_r34.py`；O4 首查 `tests/test_analysis_graph_publication_r44.py`。
O2、O3、O5 的精确测试范围在实施时按实际消费者确定；新增或修改测试遵循
[marivo-test-fixtures](../../../.agents/skills/marivo-test-fixtures/SKILL.md)。

真实 provider 行为变更使用受影响的 `make runtime-test TESTS='...'` 范围；完整 Runtime
验收、安装包和发布检查仍由对应交付阶段负责。不能把静态声明等价、默认测试通过或
本次纯内存探针升级为这些层次的资格。

## 10. 基线复核

以下只读命令在上述基线上输出 `84`、`11470`，以及 `10 110`、`20 420`、`40 1640`。
它借用当前测试文件中的 source builder，不执行测试函数；该私有辅助函数变化后应随
实现重新选择等价 fixture。此命令用于复核历史基线，不是优化后的黄金行为要求。

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B - <<'PY'
from runpy import run_path
from unittest.mock import patch

import marivo.analysis.core.graph as graph
from marivo.analysis.core.rules import PartsTransport
from marivo.analysis.methods.registry import REGISTRY

print(len(REGISTRY.registrations))
print(sum(len(item.implementations) for item in REGISTRY.registrations))
source = run_path("tests/test_analysis_graph_r33.py")["_source"]
for depth in (10, 20, 40):
    root = source()
    with patch.object(graph, "_validate_method", wraps=graph._validate_method) as checks:
        for _ in range(depth):
            root = graph.method_node(
                (graph.Edge("quantity", root),),
                PartsTransport("view", root.signature.domain, (), True),
                value_type=root.value_type,
            )
        print(depth, checks.call_count)
PY
```
