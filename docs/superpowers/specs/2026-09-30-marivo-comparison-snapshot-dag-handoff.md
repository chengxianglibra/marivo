# Handoff：递归比较定义快照的 DAG 编码优化

日期：2026-09-30。状态：统一 DAG 编码已实施，列明的协议、Runtime 与全仓验证已通过。

本文件来自 R6.2 实施期间的独立讨论，只记录后续优化任务，不改变主线程的实现范围、验收状态或当前 owner 契约。主线程工作树仍在变化，接手时须重新核对代码和版本。

下文保留优化前的交接背景；当前实现与验证结果见末尾实施记录。

## 问题与目标

递归比较需要保留冻结的计算定义，但不天然要求保存重复展开的定义树，也不天然要求压缩。

例如 `(Aug − July₁) − (July₂ − June)`，固定续算必须保留有序端点、递归量模板、时间角色、单位、对应政策和独立捕获身份。July₁ 与 July₂ 即使定义相同，也不能被合并。恢复这些事实不得依赖当前 Semantic catalog、来源重读或 Artifact 历史追溯。

这里的“快照”是计算定义和绑定事实的冻结表示，不是业务表或完整查询结果的复制。端点数值、presence、实际状态部件及其 receipts 有各自的存储职责。

当前实现递归序列化图节点，共享子图会在多个分支重复出现；固定续算另有显式端点定义。压缩降低了编码体积，却没有消除结构性重复。目标是评估并实施按捕获身份保存一次节点定义的图编码，减少体积、编码/解码和校验成本，同时保持现有语义与完整性边界。

## 当前代码线索

以下是记录时的只读核对结果，行号和细节以接手时源码为准：

- `marivo/analysis/materialization/graph_protocol.py`：`freeze_graph()` / `thaw_graph()`；比较根使用 `comparison-v2:` deflate/base64 编码，展开上限 4 MiB；continuation snapshot 的持久化预算仍为 256 KiB。
- `marivo/analysis/materialization/graph_composition.py`：`freeze_endpoint()` / `thaw_endpoint()`；端点也使用压缩定义，解压边界为 256 KiB。`comparison_endpoints()` 校验其与精确输入定义指纹的对应关系。
- `marivo/analysis/core/rules.py`：部分规则参数携带 `endpoint_definitions`。
- `marivo/analysis/materialization/graph_publication.py`：共同发布协议保存 continuation、状态和部件 receipt 绑定。
- `marivo/analysis/core/graph.py`：现有节点身份、图拓扑和指纹规则，是复用图表示时的首要检查对象。

尚未量化各类重复所占比例，也未证明某种新的 wire format 优于现有编码；不能把本 handoff 当成已经完成的性能分析。

## 建议工作顺序

1. 先测量。分别记录纯定义 JSON、端点定义、最终 continuation 的字节数，唯一节点数与序列化节点出现次数，以及构造、编码、解码和校验耗时。覆盖单层比较、嵌套 Difference、原始 ratio/linear 端点和共享分支递增的图。
2. 检查现有统一 graph/Store 7 协议能否表达节点引用，优先复用；不要新增比较家族专用执行器或平行恢复系统。
3. 评估规范化表示：根节点引用、每个节点一次的定义表、有序且带角色的输入边。端点保留定义应纳入同一明确的定义闭包，避免继续在多个参数里复制整个子树。
4. 按显式捕获身份去重。相同身份出现不同定义必须拒绝；不同身份即使定义相同也必须保留。不得用表达式等价、数值相等或内容哈希替代捕获身份。
5. 明确规范编码、节点引用校验、资源预算和版本切换，再改生产链与恢复链。压缩是否继续存在，应由测量和预算决定，不能以“能压缩到限制以下”作为结构优化完成依据。

## 必须保持的边界

- 保留有序左右角色、递归模板、设计/对应政策、原始桶坐标、独立目标实现、presence 与四种 Cell 的区别。
- 固定续算只消费精确冻结定义及实际保留部件，不回源补定义、补状态或重新选择成员。
- 定义引用不能赋予结果原本没有的 rollup、share、attribution 或其他续算能力。
- 不把节点身份直接改成内容寻址，也不未经分析改变 fingerprint、执行键、receipt、实现契约版本与缓存有效性的关系。
- 不简单提高 256 KiB 预算来掩盖重复；保留对节点数、边数、嵌套/解码成本及压缩展开的明确限制。
- 如改变持久化格式，遵循 Runtime owner 的版本和重新执行修复规则；不自行增加旧格式双读、迁移或兼容别名。
- 不编辑 AGENTS.md 或 packaged skills；不提前扩展 R6.3–R6.7。必要的文档、类型和协议测试随实际格式变化更新。

## 验收条件

- 有前后对照数据，能解释节省来自消除哪些重复；共享图的编码节点数随唯一节点数和边数增长，而非随递归展开次数增长。
- 同一共享节点 round-trip 后仍是同一图节点；两次独立 July 捕获仍是不同节点，并维持原来的比较结果及资格。
- 覆盖重复 ID 冲突、缺失引用、非法角色、循环引用、无关节点注入、损坏/非规范编码与资源超限。非法元数据在缓存命中和执行前拒绝。
- DuckDB table、Parquet 的公共比较结果及端点不变；新进程禁用来源与 DuckDB 后，固定续算保留相同定义、原因、部件和实际能力。
- 不增加构造期业务读取；共享节点每次 source 执行只实现一次，新的顶层 source 执行仍重新求值。
- 新增或修改测试时使用 `marivo-test-fixtures`，先运行目标协议/比较测试与必要 Runtime 测试，再运行 `make check-agent`。分别记录通过、失败、跳过和未验证项；不运行 release-check 或启动 MinIO。

## 接手前阅读

- [R6 实施计划](2026-09-30-marivo-full-algebra-dsl-r6-implementation-plan.md)
- [R6 迁移账本](2026-09-30-marivo-full-algebra-dsl-r6-migration-ledger.md)
- [Analysis 定义与比较契约](../../specs/analysis/python-analysis-design.md)
- [Runtime 状态与恢复契约](../../specs/analysis/session-state-and-runtime.md)

本任务的交付物应是可验证的编码优化及证据；本文件本身不授予任何新资格，也不表示当前压缩方案已被决定删除。


## 实施记录（2026-09-30）

已按统一 graph 格式实施，而非新增比较专用编码：`graph-dag-v1:` 保存
`marivo.analysis.graph_dag/v1`，continuation 与 execution-key 升为 v2。
`MethodNode.retained_endpoints` 作为定义引用纳入同一闭包，替代规则参数里的
整树字符串；执行拓扑仍只消费实际 inputs/sources。

持久化 256 KiB、展开 4 MiB 的预算不变；增加 4,096 节点、16,384 引用及
128 层定义深度上限。未改变 Store 7、数值方法或 state/part 版本；未增加双读、
迁移、公开 API 或 R6.3–R6.7 资格。

初始 R6.2 修改已提交为 `377bc341f8`，作为本次前后测量基线。
前后测量、复现方式及最终验证状态记录于
[统一 DAG 快照证据](2026-09-30-marivo-snapshot-dag-evidence.md)。

最终验证：`make check-agent` 全范围门禁通过（两个 worker，5,514 项默认测试通过、
5 项跳过；lint、410 模块类型检查与 API 文档构建通过）。比较 Runtime 矩阵 81 项、
补充发布/关系 Runtime 20 项、加强的冷恢复 2 项均通过；规范节点编码精确比较收紧后，
相关 5 项 Runtime 再次通过。原始压测与最终代码摘要见证据文件，未扩大数值或续算资格。
