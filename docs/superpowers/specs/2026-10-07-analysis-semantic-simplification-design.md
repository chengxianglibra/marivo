# Analysis DSL 与语义层简化方案

Date: 2026-10-07

Status: O1, O2a, the selected O2b consumer batches and the narrowed O3 observation/forest reuse are implemented within the recorded support and validation scope. O4a and O4b are implemented within the validation scope recorded below. O5a and O5b are implemented within the validation scope recorded below. Other O2b candidates remain proposed.

Baseline: `panda@ccbed62962e21beef5778c738037e62d912ec8fb`.

优先收敛重复图校验、物理实现声明和语义规范化，再处理持久化依赖与表达式书写约束。
目标是减少同一事实的重复推导、重复表示和重复检查，同时保持结果正确性与清晰的失败边界。
前三项先按等价内部整理实施；改变支持范围、恢复协议或作者语法的部分分别交付。

O1、O2a 已按本文的等价内部整理边界实施，并更新对应私有构造、编译交接与消费者规范。
O2b 首批扩大 SQLite Funnel 的直接 string/int64 Subject 轴组合；后续四批扩大
observation 长路径、SQLite 历史轴、SQLite Anchor 组件和 native distribution 输入。
新增 remote ordinary Count 声明及原有冻结集合的边界分别记录。O3 按第 6 节修订边界实施；
O4a 与 O4b 已实施；其他 O2b 候选仍是优化提案；O5a 与 O5b 已实施；本轮不表示
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
| O3 语义编译 | 观察分流、组件构造重复解析，runtime forest 重复降低 catalog 根 | 高 | 一次观察传递契约；单次 builder 复用成功的精确 catalog 根 |
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
- O3：[runtime metric lowering](../../../marivo/semantic/runtime_metric_lowering.py)、[graph observation](../../../marivo/analysis/materialization/graph_observation.py)、[public DSL](../../../marivo/analysis/public_dsl.py)、[Anchor observation](../../../marivo/analysis/materialization/graph_anchors.py)。
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

### O2b 首批实施记录（2026-10-07）

- 实施基线为 `panda@1d9fedea3d`。仅扩大 SQLite native main-table、int64 occurrence
  identity、UTC-us 路线上的直接轴组合；任何非空、去重、有序的直接 string/int64
  Subject 轴元组均可进入原准备与本地归约消费者。Subject 必须未版本化；沿用既有
  图与快照预算，没有新增轴数、排列或输入行数限额。
- 直接轴按每个轴的物理类型与路径事实准入，删除固定类型序列白名单。
  `funnel.entry_axes` 与 `funnel.reduce` 使用同一消费者规则。历史轴继续只支持既有
  单字符串轴、单跳 UTC DATE snapshot/validity profile；历史轴组合、多跳、版本化
  Subject、其他轴类型、空轴来源归约、mixed source/fixed 与异构 shape 不随本批放开。
- 复用逐轴入口捕获、完整实际元组归约及 retained comparison/allocation；Null 类别与
  字面量 Other 保持不同含义，不生成笛卡尔积组。新增反向类型排列、同类型轴对、三轴
  及 17 轴准入回归；实际 SQLite 用例覆盖四种新增短轴组合和 17 轴长序列、空结果与
  七项计数。
- 空结果验收发现原 Funnel exchange 的零行 accepted 状态列被 Arrow 推断为 null。
  该消费者现在显式声明 string 状态列；非空输出、持久化协议与版本保持原样。
- nullable int64 与 hierarchy 非活动前缀的读取反例确认默认 pandas 转换会将整数轴
  转为 float64，损失超过 2**53 的精度。带 nullable int64 的 Funnel 结果现在使用
  Arrow-backed pandas 类型；直接轴、joint/hierarchy 与冷恢复均检查精确整数坐标。
  仅改变公开读取表示，原主表、retained state、Store schema 与摘要规则保持原样。
- 84 个方法、11,470 条基础声明及其 exact key、实现 ID/version、checks、parts、
  精度、资源和 qualification metadata 没有改变。O2a 冻结声明与 specialization oracle
  保持原样；新增参数资格由本节和独立运行测试记录，不改写 R9.3 或完整 C12 结论。
- 原 `.funnel()` docstring 由 native Help 展示新增约束；结构化错误、owning specs 和
  latest 英中三轴示例同步。无公共导出、新 Help target、registry 或路线回退。
  `AGENTS.md` 与 packaged skills 保持原样。

首轮验收结果：

- 受影响消费者/Funnel 默认回归为 160 passed；补齐英中示例、Help 与导出回归后为
  175 passed（10.94 s）。O2a 冻结声明和 specialization oracle 继续通过。
- 最终 `make check-agent` 通过：766 个文件的格式/lint、import contracts、335 个源码
  文件的 typing、默认测试 4,746 passed / 1 skipped（58.15 s），以及 API 文档构建。
  初次 broad run 因新增双语代码块使旧数量断言 27 与实际 28 不符而失败；该断言已
  同步，并通过上述完整重跑。
- 8 个受影响生产/测试模块的定向 typing 通过；该定向调用使用
  `--follow-imports=silent` 隔离未修改测试 helper 的既有类型问题，被选模块本身完整检查。
- 最终定向 Runtime 为 10 passed（295.64 s）：四种新增直接轴组合的 SQLite 实际执行、
  空结果、七项计数与聚合守恒，非法输入/空轴来源路线的提前拒绝及取消清理，三轴的
  独立进程 fixed/cold 续算，以及既有 SQLite int64/string、snapshot/validity 回归。
  英中三轴示例实际执行；移除源文件与模型后读取、比较、joint/hierarchy 分配、
  reconciliation 与 Findings 保持一致，冷进程重建图命中不新建 Run 或重跑 kernel，
  新 top_k continuation 只新增一个 Run。超过 2**53 的 nullable 整数坐标保持精确。
- 外部 provider Runtime、完整 Runtime、安装包、真实 Agent 与发布验收未运行。
  首轮 17 轴仅有静态准入证据，其实际执行由下述审查修复回归补齐。

审查修复：

- 17 个唯一直接轴、4 个 Subject、约 10 KiB 图快照的反例通过准入后，在 SQLite
  执行阶段触发 `at most 64 tables in a join`。原因是逐轴 lowering 重复展开 occurrence
  和 Subject JOIN；替换回基线准入函数时，同例在 Run 分配前拒绝。
- 同一未版本化 Subject、相同实体与来源绑定的直接轴现在共享入口映射 JOIN，并按
  原轴顺序投影所有列。完整路径、exact entry mapping 及每个非空 Dimension 的检查
  保留；历史路径继续逐轴处理。不增加轴数限额，不改变实现身份、Store 编码或路线。
- 新增 17 轴 SQLite Runtime 回归复用短轴组合的独立预期，覆盖真实 Null、字面量
  Other、大 int64、完整元组、七项计数、初始 Undefined、空结果和资源释放。
  将该测试进程中的 lowering 换回 `1d9fedea3d` 实现后，测试在 1.32 s 内复现 JOIN
  上限失败；当前实现同一用例为 1 passed（4.96 s），确认回归直接覆盖审查问题。
- 修复后的受影响默认测试为 160 passed（9.05 s）；两个修改模块的定向 typing、lint
  和 import contracts 通过。定向 Runtime 为 13 passed（309.40 s），包括新增直接轴
  组合与 17 轴、取消清理、三轴断源恢复，既有 SQLite 直接轴与 snapshot/validity，
  以及 DuckDB table/parquet 分组归因回归。其余 provider、完整 Runtime、安装包、
  真实 Agent 和发布验收仍未运行。
- 修复后 `make check-agent` 全部通过：766 个文件的格式/lint、import contracts、335
  个源码文件的 typing、默认测试 4,746 passed / 1 skipped（51.95 s），以及 API 文档构建。

### O2b 其余消费者实施记录（2026-10-07）

实施基线为 `panda@a7b0d9b7af`。以下四批沿用现有 API、唯一 MethodRegistry、
执行路线、Store 7 和既有实现身份。用户补充要求实现四个远端 provider 的普通
`members.observe(ms.count(...))`，因此仅新增 8 条 `metric.count` 声明：
PostgreSQL/MySQL/Trino/ClickHouse × int64/string member carrier。它们使用明确的
`o2b.<backend>.metric.count.ordinary.<carrier>@v1` 身份和原 graph lowering 消费者。
原 11,470 条声明的顺序、exact key、身份、checks、parts、资源与完整 specialization
oracle 保持冻结；测试独立排除并核对这 8 条增量，不改写原冻结数据。

#### 普通 Metric/Count observation 长路径

- 构造和准入删除普通两跳、relative 三跳的固定限制。路径仍须完整端点、连续连接、
  显式完整 join keys 和有向 to-one；普通 Entity 保持未版本化，relative 保持原版本捕获。
  不新增跳数限额，图/快照预算及真实 provider 能力继续适用，不丢弃路径或切换路线。
- 六个后端的四跳 sum/count 使用共享语义建模 fixture 和独立结果预期：完整 member key、
  `2**53 + 3` 精确求和、贡献数 `[2, 1, 0]`、过滤后 `[2, 0, 0]`、窗口外行排除与
  空 member 的 Defined zero。四个远端分别覆盖 int64 和 string member，并通过真实
  registry selection 核对新增 Count 身份和按完整 key 保存的 original count state。
  Count 每个合法贡献计 1，不通过 DISTINCT 掩盖重复身份或 fanout。
- PostgreSQL 的复合 member key 另以 `(1, "a")`、`(1, "b")` 和 `(2, "c")` 核对四跳
  sum/count、完整 original state、第二坐标过滤和 fixed/cold 恢复，防止按首坐标折叠。
- DuckDB/SQLite 另覆盖三跳、九跳及 relative sum/count。四跳结果在 source fixture
  关闭并删除源对象、模型后进入独立 fixed/cold 进程，验证 retained 读取、合法 rollup、
  Findings/contract 快照和重复命中；禁止源、语义执行和冷命中的本地 kernel，资源为零。
  Runtime owner：`tests/analysis/graph/test_observation_paths.py`。

#### SQLite 历史 Funnel 轴

- 逐轴准入允许 string/int64 直接/历史组合、多条路径和多跳 to-one；Subject 未版本化。
  路径中的每个版本化 Entity 只接受 UTC 原生 DATE snapshot，或 closed-open validity、
  NULL open end。timestamp、其他时区/closure/open end 和其他轴类型继续结构化拒绝。
- 同一完整路径共享映射；不同路径分别捕获并按完整 occurrence identity 精确装配。
  入口装配在 Journey matching 前完成，不按轴数展开重复 JOIN。逐跳缺失、重复、
  非空与版本重叠检查保留；缺失、重复或 foreign occurrence key 拒绝装配。
- 实际五轴组合覆盖共享 snapshot 路径、历史 int64、中间 validity Entity、三跳 leaf
  和直接 Subject 轴，验证精确 snapshot、validity 边界、真实 Null、字面量 Other、
  大 int64、authored order、七项计数与初始 Undefined，不生成笛卡尔积坐标。
  三轴 period comparison 在独立 fixed/cold 进程继续 joint/hierarchy 分配、reconciliation、
  Findings、新 top_k 和重复命中。缺失/重复 snapshot 与 validity 重叠在发布前失败，
  无 Artifact 或连接/临时快照遗留。Runtime owner：
  `tests/analysis/journey/test_sqlite_funnel_direct_axes.py`；既有 snapshot/validity owner
  `tests/analysis/journey/test_journey_consumers.py` 保留相邻回归。

#### SQLite Anchor observation

- 准入允许 count、int64/float64 additive sum 的 zero/null、Metric slice、ratio 和 signed
  linear，沿用原类型、单位和 relative-observation 规则。mean、fold、cumulative、distinct、
  contribution coordinates、Decimal/Duration 等新 carrier 继续由真实契约拒绝。
- 非 DuckDB preparation 每个组件捕获独立 flat candidates，并记录该表达式实际来源。
  全部源读取完成后才按序打包为 typed `uses_i` 列表交给既有本地消费者；不做候选行
  Cartesian join 或统一数值转换，共享原快照 authority 和 deadline。
- 实际六种 observation 覆盖大整数、float null、过滤、同根 ratio、异根 signed linear、
  空分母 Undefined、同刻 business order、own-anchor 排除、窗口重叠和组件候选计数。
  独立 fixed/cold 进程在模型/源删除并禁止源与语义执行后读取、续算并命中。
  第二组件捕获取消、所有源捕获后的 deadline 和 int64 溢出均不产生部分发布，资源为零。
  Runtime owner：`tests/analysis/journey/test_anchor_consumers.py`；既有 Event/Journey
  origin、elapsed/calendar、DST 回归由该 owner 与 `test_journey_anchors.py` 覆盖。

#### Native distribution 输入

- distinct 按方法接受现有 int64/float64/string/boolean/date/timestamp；exact 与 authored
  approximate 仍走各自原声明。quantile 接受 int64/float64；仍是 bounded 单列输入，
  不扩大复合 distinct、窗口或后端/方法组合。
- PostgreSQL/MySQL/Trino 的 exact Decimal distinct 保留相差 `0.000001`、超过 `2**53`
  的原系数；explicit approximate Decimal distinct 使用原 native 操作，包含 ClickHouse。
  DuckDB 沿用其 Decimal carrier。Null 忽略、空 distinct 为 Defined zero、空 quantile
  为 Null；finite Decimal 检查只返回违规标记，避免 NaN 在检查响应中先触发 Arrow 转换。
  Decimal 上下界通过精确文本 literal cast 构造，避免 SQL compiler 对负大 Decimal
  literal 的舍入。没有转换实际输入数值。
- MySQL BOOLEAN 的实际 carrier 是 int8，继续在读取/Run 前拒绝；SQLite float-backed
  NUMERIC 不获得 Decimal 资格。native Decimal quantile 结果无法保留 declared Decimal
  carrier，Duration lowering 需要 DuckDB `epoch_us`，这两类继续结构化拒绝。
  SQLite/MySQL quantile、Trino exact quantile 和 ClickHouse exact distinct 继续提前拒绝，
  不用 fallback 或隐式转换授予资格。
- 每个新增 native method/type/backend 资格有真实 fixture 结果与提交 SQL 证据；
  VARCHAR 宽度及 timestamp 精度保留在来源表达式。固定 retained 读取和 where 续算禁止源
  读取；代表性新增 carrier/quantile 另在源对象、模型删除后的独立 fixed/cold 进程中核对
  parts、Findings、合法新归约和重复命中。原分布量不获得 original rollup/attribution 权限。
  Runtime owner：`tests/analysis/graph/test_distribution_consumers.py`；原 DuckDB
  float/Decimal 三进程恢复继续由 `test_distribution_carriers.py` 覆盖。

默认门禁与后端证据：

- 受影响默认测试首轮 207 passed；补充逐方法 carrier 边界、完整 occurrence 装配与双语
  示例后，定向默认测试 179 passed（16.87 s）。既有 O2a oracle、非法 shape/source/fixed
  边界和 Help/动态 continuation 有界测试保留。受影响源码 typing 通过；9 个新增或修改
  测试/helper/worker 的 typing 通过，后者用 `--follow-imports=silent` 隔离未修改测试
  helper 的既有导入类型问题，被选模块完整检查。
- 首轮 `make check-agent` 全部通过：769 个文件格式/lint、import contracts、335 个源码
  文件 typing、默认测试 4,749 passed / 1 skipped（70.71 s）及 API 文档构建。
  后续 carrier 和失败反例的最终门禁结果在本节末记录。
- 后端见证来自既有 `marivo-multisource`：PostgreSQL 17、MySQL 8.4、Trino 483 Iceberg
  与 ClickHouse 26.3.33.24 MergeTree；Trino/ClickHouse 串行切换。端口探针及 skip 不计通过。
  长路径 sum/count 的六个后端都实际执行；native scalar/float 扩展首轮为 43 passed
  （含 2 个 MySQL BOOLEAN 明确拒绝）和 ClickHouse 12 passed（包含既有边界/相邻回归），
  并运行 exact Decimal 与其拒绝边界。各次定向运行有重叠，不相加为独立用例总数。
- latest 英中新增四个相同可执行示例分别在长路径 Count、历史 Funnel comparison、
  过滤 Anchor 和 scalar distinct Runtime 中执行；原 API docstring/native Help、结构化
  expected/repair、owning specs 和动态 continuation 同步，不新增公共入口或 renderer 清单。
- mixed source/fixed、异构 shape、其他 O2b 候选及 O3–O5 未扩展。完整物理/存储矩阵、
  完整 Runtime、安装包、真实 Agent、发布验收未运行；本轮证据不授予这些资格。
  `AGENTS.md` 与 packaged skills 未修改，不自动提交或推送。

最终复核结果（各行仅表示其选定范围；运行间有重叠）：

| 范围 | 实际结果 |
| --- | --- |
| 长路径及 native Count | 六后端四跳、DuckDB/SQLite 三/九跳、四远端 string key 均有执行见证；最终 Trino int64/string 2 passed（67.10 s），PG/MySQL string 2 passed（62.56 s），ClickHouse int64/string 2 passed（74.45 s），PG 复合 key 1 passed（28.08 s），均包括相应断源 fixed/cold |
| 历史 Funnel | 新历史组合、装配和缺失/重复/重叠失败首轮 5 passed（234.49 s）；最新英中历史三轴示例及独立恢复 1 passed（287.11 s）；既有 SQLite direct/snapshot/validity 5 passed（379.68 s）；最终直接轴短组合/17 轴、取消清理和 direct fixed/cold 7 passed（222.51 s） |
| Anchor | 六种新增 observation、组件取消/deadline/溢出和独立恢复最终 1 passed（84.90 s）；现有 SQLite Event/Journey Anchor 与 DuckDB float/Decimal distribution 相邻三进程回归 9 passed（702.40 s） |
| Native distribution 恢复 | SQLite timestamp/string、PG/MySQL Decimal、PG float quantile 等选定集合 7 passed（124.64 s）；ClickHouse boolean distinct/float approximate quantile 2 passed（54.95 s）；Trino float approximate quantile 的 fixed/cold 在其后 4 passed / 1 failed 的运行中通过，唯一失败为 DuckDB Decimal 检查 literal，已修复复核 |
| Decimal 后续补测 | PG/MySQL/Trino approximate Decimal 和 Trino float quantile 首轮 4 passed / 1 failed；DuckDB 负大 Decimal literal 反例修复后 1 passed（5.45 s），ClickHouse approximate Decimal 1 passed（12.85 s）；最终 PG/MySQL Decimal exact/approximate、NaN 和 quantile 拒绝 6 passed（49.03 s），Trino Decimal exact/approximate 2 passed（18.74 s） |
| 默认门禁 | 定向 consumers/双语示例/Help 222 passed（13.97 s）；最终 `make check-agent` 全部通过，769 文件格式/lint、import contracts、335 源码 typing、4,769 passed / 1 skipped（54.31 s）及 API docs；复合 key 后其测试 typing/lint 单独通过，精度说明澄清后 Help/双语示例 49 passed（7.43 s），完整 to-one route 的结构化修复说明更新后 consumers/normalization/Help 239 passed（9.41 s） |

PostgreSQL NaN 初次反例在检查响应的 Decimal-to-Arrow 转换中先失败；改为只返回
违规标记后，按有限值契约报错并验证无部分 Artifact、资源为零。DuckDB 负 Decimal
边界的初次反例来自 compiler literal 舍入，修改上下界 literal 的表达式后同例通过。
以上失败已被对应修复和重跑覆盖，不计为通过的独立用例，也不隐藏在累计 green 数量中。
最终环境恢复起始状态：Trino 与其 PostgreSQL catalog、独立 PostgreSQL/MySQL analysis
服务 healthy，ClickHouse 与两 shard 停止；未替换或删除其 volumes。

## 6. O3 在一次观察中复用语义解析

### 修订原因与边界

原提案的 `CompiledSemanticState` 生命周期缓存不能作为当前契约下的等价优化实施：
registry 只冻结外层映射；ordinary Measure sidecar 保留原始 Python callable，helper
仍可读取或修改外部状态。实测同一加载状态中的 helper 首次返回列、后续返回聚合时，
第二次规范化必须拒绝。成功结果跨消费复用会吞掉该错误。compiled dependency inventory
也未覆盖 weighted-mean 的全部 Measure 和部分业务时间事实，不能用作完整缓存键。

本次收缩为两项内部整理，不改变 load、作者表达式或依赖清单契约：

1. 一次公开 `observe()` 在原有校验顺序的入口解析 `TargetMetricContract`，将同一对象
   显式传给分流和各组件构造。普通、grouped 与 Anchor 入口遵循同一责任划分；后续
   独立观察仍重新解析。`complete_during` 的短路拒绝顺序保持原样。
2. 一个 `_RuntimeGraphBuilder` 内按精确 catalog Metric path 保存成功 lowering 的
   规范化图，不保留未使用的单根 dependency digest 或 identity 投影。registry 和 sidecar
   由该 builder 固定持有，调用结束即释放。
   每次使用仍记录依赖、重新映射 occurrence path；有序根、重复位置、catalog authority、
   runtime 标签和 expression 预算保持原样。不同 Ref 即使 value graph 等价也分别 lowering。

不存失败异常、连接、schema、行数据或 readiness 的外部事实。每个组件继续检查当前
schema、表达式绑定和物理类型，未知类型与 Decimal 宽度保持待绑定状态。没有生命周期
缓存、公共 token、新 Help 路径或 Store 协议变更，也不深复制或冻结任意 Python helper。
一般 callable 的固定加载解释需要单独设计语义契约，不属于本次优化。

出口包括：一次观察只解析一次；同一 forest 的精确 catalog 根只成功 lowering 一次；
规范化图、依赖摘要和原有输出保持等价；重复 occurrence 不绕过预算，标签不按 value
equality 合并；后续消费重新解释 callable，schema 漂移仍在业务读取及 Run 分配前拒绝。
记录纯构造耗时、规范化次数、builder lowering 次数和构造峰值内存；这些不是执行加速
或完整后端资格的证据。

### 实施记录与构造探针（2026-10-07）

基线为 `panda@b2afa86e20b9d241e027a3384ead89f770cdca03`，通过仓库外 temporary archive
运行基线包，当前工作树运行新实现。两者使用相同的 SQLite lifecycle fixture、成员、
时间窗和 route；每项串行构造五次取中位数，不调用 `.execute()`。计时与一次额外的
`tracemalloc` 构造分开；内存为 Python 分配峰值，不代表 RSS 或后端 workspace。

| 输入 | 契约解析次数，前 → 后 | catalog forest lowering 次数，前 → 后 | 构造中位数 ms，前 → 后 | 规范化中位数 ms，前 → 后 | Python 峰值 KiB，前 → 后 |
| --- | ---: | ---: | ---: | ---: | ---: |
| catalog sum | 2 → 1 | 2 → 1 | 6.143 → 5.521 | 1.066 → 0.541 | 124.3 → 93.3 |
| runtime ratio | 5 → 1 | 10 → 2 | 20.259 → 13.354 | 8.606 → 1.779 | 274.3 → 160.5 |
| runtime linear，2 分量 | 5 → 1 | 10 → 1 | 19.721 → 13.914 | 8.299 → 1.395 | 235.5 → 172.2 |
| runtime linear，8 分量 | 11 → 1 | 88 → 1 | 99.558 → 47.791 | 51.748 → 2.057 | 771.3 → 275.0 |
| runtime linear，32 分量 | 35 → 1 | 1,120 → 1 | 774.473 → 220.147 | 572.832 → 5.579 | 1,950.1 → 664.7 |

linear 输入重复同一 revenue Ref；ratio 使用 revenue/count 两个不同 Ref，所以保留两次
catalog lowering。去重没有删除任何分量。固定 Session 和节点身份的前后探针中，
以上五项定义 fingerprint 与完整 `freeze_graph()` 字节均相同。构造之外的执行、规划、
Store 读取和真实远端后端速度未作对比。

全部根都不同的纯 lowering 探针中，2/32/128 个 catalog Ref 的 Python 峰值分别从
52.8/420.2/1,474.1 KiB 变为 54.0/441.5/1,560.3 KiB。128 根返回后保留分配均为
760.9 KiB；单次 builder 的图复用表仍增加约 86.2 KiB 峰值。该成本受当前 expression
预算约束，调用结束后释放，不能把重复输入的内存收益推广到全部不同根的输入。

回归由 `test_semantic_metric_graph_lowering.py` 与 `test_metric_observation_resolution.py`
分别保护规范化值/authority、标签、预算、后续 callable/新加载依赖，以及公开构造与
独立数值结果、completeness 拒绝顺序和当前物理类型检查。复用表属于单次 builder；
没有跨调用保留缓存。删除了多分量分流、组合构造及每个组件的重复规范化，新增一个
私有组件构造函数承接契约，独立的 `observe_members()` 入口仍自行解析一次。

最终 `make check-agent` 通过：335 个类型检查目标、lint/import contracts、API 文档构建，
默认测试 4,776 passed、1 skipped（46.69 s）。受影响语义最小集合 46 passed；
针对性 Runtime 集合 21 passed（225.61 s），覆盖独立多根 ratio、fixed/live 拒绝、
Decimal、Anchor 组件断源与冷恢复、相对模板及 route authority。复用表收缩为仅存图后，
新增 Runtime 模块再次 7 passed（57.53 s）；新模块以 `mypy --follow-imports=silent`
单独检查通过。完整 Runtime、发布 wheel 与真实远端后端资格不属于本次验收。

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
该语法已按下述实施记录支持：

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

### O5 implementation record (2026-10-07)

- O5a captures one function AST per expression compilation and reuses it for
  validation, binding collection, physical columns, normalized identity, column
  access analysis, and assembly root/temporal inspection. Hand-built internal
  bodies retain source-based inspection; no process-global cache was added.
- O5b supports fresh single-name assignments in source order followed by one
  final return for Dimension, TimeDimension, Measure, and decorator Metric.
  Local names normalize by definition order; existing single-return hashes stay
  unchanged. Original Python execution preserves sharing without substitution.
- Reassignment, parameter/symbol shadowing, forward/undefined names, callable
  aliases, table aliases, multi-target/unpacking/writing, annotated/augmented
  assignments, nested bindings, and control flow remain rejected. Event retains
  its separate grammar. Non-root aggregates through local values are rejected.
- Native constraints/Help, authoring docstrings, semantic object model, and
  latest bilingual examples are aligned. Public exports, persistent formats,
  AGENTS.md, and packaged skills are unchanged.
- Parsing probe: one AST parse for compiled local-binding bodies, with no new
  parse during subsequent column analysis. A pre-change single-return hash is
  pinned independently. Original callable execution is covered by both a
  single-evaluation witness and loaded DuckDB materializer result oracles.
- Validation: targeted binding/validator/materializer/root tests passed (94);
  final binding/Help tests passed (71). Final `make check-agent` passed: lint,
  typing (326 modules), default tests (4765 passed, 1 skipped), and API docs.
  Focused Runtime public J2/J3/J4 documentation workflows passed (3).
  Full Runtime, installed package, real Agent, and release acceptance are
  outside this delivery.

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
| 语义复用 | 一次观察解析次数、单次 builder 的精确根复用、后续调用重解释、依赖与顺序隔离 |
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


### O4 implementation record (2026-10-07)

O4a and O4b use the frozen-reader consumer inventory in
`docs/specs/analysis/python-analysis-design.md`. Store 7 retains its layout;
Artifact descriptors use v2 and continuation metadata uses v3. Historical DAGs
remain intact. Old descriptors require re-execution, without migration or file
deletion. Metadata interpretation is reused through operation-owned validation
handles; receipt bytes are independently verified on every read.

Fixed reads and public Materialized construction no longer select production
implementations. Historical semantic restoration belongs to typed continuation
and mechanical continuation disclosure. Completed evidence carries its frozen
Fact and is checked against the retained obligation inventory. The production
physical plan remains identity material rather than an executable recovery plan.


Validation for this implementation:

- `make check-agent`: 4,753 passed, 1 skipped; lint, typing and API documentation passed.
- Materialization and snapshot regressions: 99 passed, including unavailable producer registry,
  independent cold reading, descriptor identity binding, receipt replacement and obsolete formats.
- State and multiroot Runtime: 11 passed, 4 external-backend cases skipped; DuckDB and SQLite
  independent-process recovery passed. After preserving the previous `NoTime` rule, the state
  Runtime subset passed all 9 cases again.
- Installed candidate wheel: 4 offline/cold scenarios passed (relations, statistics, funnel,
  history). The final wheel was rebuilt after the time-shape and Help updates; its relation
  produce/continue/recover scenario passed again, with wheel SHA-256
  `00a05ddf88ed1627f46d9e6a1b5115448e2f9f038f62a898956e74091f055048`.
- An earlier `make release-test` unexpectedly selected the full packaging group and used a
  pre-repair wheel: 26 passed, 10 failed. The failures exposed the missing explicit-source
  traversal and were repaired; the later bounded wheel runs are the acceptance evidence.
  This work does not claim a passing full release or full Runtime gate.

No migration, publication, commit, packaged-skill edit or deletion of persisted project state
was performed. Existing staged work was preserved.


## A1 实施后的边界

[执行优化设计](2026-10-07-analysis-algebra-execution-optimization-design.md) 的 A1 已接入
声明、构造推导、调用假设和实际检查四种依据。`ExactKeys.verification` 与
`read.match_verification` 只影响其具名前提。冻结图使用 graph DAG v2；Store 8、descriptor
v3 和 continuation v4 不变。此实现不增加 nullable/finite/total 的通用声明框架，
也不实施 A2 或 A3。范围和资格以 [A1 验收记录](2026-10-07-analysis-a1-acceptance.md) 为准。
