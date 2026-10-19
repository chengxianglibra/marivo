# Analysis 模块架构与实现分层方案

Status: AnalysisFrame cutover architecture, 2026-10-19.
最终语义由 [AnalysisFrame](query-result-reuse.md) 拥有，当前代码验收由实施进度记录拥有。
本页描述最终职责划分，不把设计、局部测试或历史 provider 运行提升为新版本完整验收。

## 1. 架构结论与文档边界

公开数据对象只有不可变 AnalysisFrame，载体是待执行表达式、完整内存数据或精确存储引用。
结构决定操作合同，载体决定如何取得数据。Population 仍是有限完整域；Column/FrameAxis
绑定精确输入；Artifact 只读同一持久结果的提交与审计，不提供表格导出或计算入口。
[Python design](python-analysis-design.md) 拥有公共职责；[Runtime](session-state-and-runtime.md)
拥有生命周期与事务；[执行](lazy-analysis-dsl-execution.md) 拥有具体检查与交接。

## 2. 架构起草时的实现：保留的基础与必须调整的结构

复用受控语义定义图、来源绑定、Ibis-to-pandas、现有本地数值核、stage scheduler、
收据/Parquet、writer guard、journal 和成功事务。改变的是公开数据/载体边界以及
求值与发布职责；不建立第二执行器、通用插件框架、隐藏 SQL 引擎或兼容 Query 外壳。
旧实现中的来源 Metric/per/mean 可能仍需内部状态，必须按实际消费者保留。
退役的是原结果接收者 rollup/drill/share 及其专用资格/后继路线，不是来源数值能力。
私有 QueryPlan/SQL query 名称不因命名而必须改写。

## 3. 总体分层与依赖方向

### 3.1 数据流

```text
explicit semantic/source inputs or literal values
    -> captured source/literal Frame expression
explicit completed memory/stored Frame
    -> fixed input binding
both -> one graph plan and one prepared stage schedule
     -> complete immutable data + frozen coordinate attachments + one audit
     -> compute returns memory root
     -> persist publishes existing result through Store 12
     -> persisted Frame reads data; frame.artifact reads audit
```

### 3.2 四种内部表示

定义图拥有显式逻辑依赖；prepared facts 拥有实际来源/类型/方法事实；执行计划拥有
stage、需求、检查和消费者生命周期；完成数据拥有值、完整坐标、presence、冻结附件和审计。
它们不相互冒充：完成数据不存定义图，prepared facts 不成为公共结果类型。

## 4. 共同私有定义图

### 4.1 位置与闭合节点族

`marivo/_definition/` 与 analysis core/compiler 共享闭合逻辑定义，不保存 Session、连接、
可变容器或回调。语义作者侧捕获 Ref 后完成 finalize/resolve，再组合不可变定义。
来源输入的已完成作用域是解析权威，不刷新已有值、不重放模块。

### 4.2 Population 依赖如何跨层

Population 投影/集合与关系来源依赖保留完整元组。FrameKeys 桥接持有精确输入和明确来源
绑定；先完成选择与输入检查，再传递联合去重键，最后执行新来源。固定输入不重跑旧 Top。
`on=()` 保留零轴空域与单例域区别。来源侧资格与完整键参数传输仍由原 owner 检查。

### 4.3 身份、边和指纹

Column/FrameAxis 是精确输入句柄，不是指纹、同名声明或全局坐标服务。
存储输入由精确 Store/Session/Artifact 身份兼容共享；完整内存根拥有独立输入绑定。
定义指纹描述实际消费公式，不自动寻找旧结果。同路径定义变化本身不拒绝输入；
kind/root/grain/unit/time/null 检查与业务版本身份仍保持各自权威。

## 5. Analysis 的基本数据模型

### 5.1 终端数据与执行内状态分离

完成 Frame 只有闭合普通/日历/单层配对轴目录、命名值、非 nullable presence、
有类型/收据的必要附件与一份审计。内部聚合组件、原语义对象、生产图和来源连接不保存。
日历目录保存实际键、原名义标签/序号、名义与实际边界；配对轴保存双方冻结描述与映射。
这些数据足以解释/匹配，不通过当前模型或时区库重建。

### 5.2 统一产物生命周期

compute 只完成内存数据，无 Run/Artifact/临时保存。persist 对表达式先求值，再按已有
result ID 发布；内存 persist 不重算；存储 persist 验证提交元数据并返回自身。
纯载体转换保留结果身份、求值时间和审计。派生表达式不能拿祖先 Artifact 冒充自身产物。

### 5.3 Nullable 值、presence 与检查

普通 NA 是一个值状态，缺端点由独立 presence 表达。select/where/top 保持 presence；
reduce 丢弃 presence，只保留所选完整轴。组合增加顶层两侧 presence，再加前缀继承已有项。
严格检查消费其真实输入，Top-0、丢列、零乘数或后置过滤不能消除必要失败。

### 5.4 Nullable 的物理计算

沿用 int64、有限 float64、Decimal、日期、精确 aware timestamp 和固定 Duration 的
实际物理合同，键不能经 float。布尔不是数值量。统计独立选取有效掩码；加权/配对样本才
共享掩码。空/全 NA 的默认 sum 为 NA，显式来源 empty="zero" 才为零。
单位、除零、溢出与严格子式政策保持各自 owner。

### 5.5 执行内组件与输出布局

来源 mean/per/quantile 等在单次评价内可持有充分状态；完成 Frame 只输出明确声明的值。
输出行序是计算数据，不能在 writer/reader 按键重建，也不增加 display_order 字段。
用户名称是单段，生成路径以点分隔；内部槽/路径/值与 presence 分类是权威。

## 6. 公开封装、Session 和绑定层

### 6.1 公共对象保持薄且不可变

Session observe/read/values/frame 返回 AnalysisFrame；persisted_frame 只接受同 Session
精确字符串 ID。FrameAxis 只从当前 axes/axis(name) 获得，公开 token/name/role/key_names。
`__getitem__` 取完整值/presence 名称，coord 取单物理分量坐标；复合轴保持一个完整句柄。

### 6.2 Session 只做三类事情

Session 拥有持久身份/报告时间、显式 Catalog 来源构造，以及本地 Store/runtime 服务装配。
固定数据不捕获 Session/Catalog 或连接。结果不反向缓存；Frame/Column/Axis 强持必要
依赖，在原 Session 被 GC 后仍可使用既有本地服务，不能按路径自动重建 Session。

### 6.3 绑定层的产物

来源入口保持明确分工：observe 指标、read 单 Entity 非空字段、values 字面量、frame 一般键域。
population_from 的必填 on 将普通完整轴映射到调用方提供的 Field/Entity；不恢复旧源对象。
属性时点、身份与期间不能从 Frame 显示名猜测。Source-free fixed operands 走本地组合；
不把固定数据上传远端或把旧来源融合资格直接推广到 Frame。

## 7. Observe 与内部样本的求值组织

### 7.1 普通多量观察

一次 observe 的逻辑处理次序如下；允许融合物理操作，但必须保持这些含义和检查：

1. 解析 Metric 依赖闭包，保留每个来源规约出现及显式域依赖；常量不制造来源。
2. 绑定每个根的固有条件、共同条件、时间角色、可选身份限制和字段路径。
3. 为各根构造完整输出分类；从所选贡献的键像或显式请求确定共同输出域 K。
4. 在各根自己的贡献关系上求量和必要支持信息；各根逻辑规约，有内部样本时保留完整 (k,j) 及嵌套采样键。
5. 将各组件补齐到 K，在成功读取且无贡献时按该组件的规约规则完成。
6. 逐键执行 Metric 公式，组装命名量，在执行内保留组件和政策，输出完成值与实际审计。

多根收入/退款不能将 join 乘积上的 sum 当成原组件；组件完成值按完整键组合，不能以某一个根的键集覆盖其它根。
这不是固定物理阶段顺序：compiler 可使用已准入的单值关联、半连接、贡献键集合去重或预聚合计划，
须满足[语义 §3.7.2](../semantic/expression-model-redesign.md#372-逻辑贡献约束与物理执行策略)的身份、次序、政策和充分性条件。
源 Null 被规约忽略时，其分类键仍在键像中；字段 Null 归 None，缺字段映射不伪造成 None。
by=Population 的覆盖检查针对实际被消费贡献，不能先做 inner join 将域外贡献删掉后宣称覆盖完整。

内外键处理是独立的窄步骤：键域关系只承载轴/键，组件关系承载该根的键/值/状态，最终才组装多量表。
它们是同一计划中的关系，不是需要分别发布的多个 Artifact。
键和值来自该组件的同一合法分类输入，不能一个扫描求键、另一个扫描求值后忽略相互约束。

### 7.2 per 使用展开键，不做全局客户预聚合

`m.per(samples).mean()` 的中间域是 `(k,j)`：先在一个外部格子 k 内为每个样本 j 完成整个 m，
再对这些完成值求 mean。建议用一个闭合 PerReduce 定义，由编译器展开成键准备、子指标求值和外层规约阶段。
不要把 per 编译成“先在全局算客户均值，再把它 join 到地区”。

```text
external output K
  -> sample keys J(k)
  -> expanded coordinates (k, j)
  -> evaluate each child metric at (k, j)
  -> finish the complete child formula
  -> apply the outer reducer within each k
  -> output one nullable value per k
```

| 样本入口 | J(k) 的取得方式 | 必须保留的区别 |
| --- | --- | --- |
| per(Field/字段元组) | k 内相关贡献根完整字段键像的并集 | 键像不因某个组件没有有效值而缩小；未出现类别不补齐 |
| per(Population) | 显式域中与 k 相容的全部键 | 域外贡献不属于问题；名单内无活动样本仍按子指标政策完成 |
| per(状态 TimeField) | 声明采样合同在本次期间所要求的样本 | 明确缺测传播，不以实际出现时点替代应有采样 |

共享轴必须取相等键；同一 j 可出现在不同 k，不要求 j 唯一决定 k。
显式内部域不创建额外外部格子。合法零轴非空域产生每个 k 的一次子求值，零键域没有内部样本；
两者都不等于子指标空贡献。Field/Member 复合域按完整绑定能力准入，首期内部 Calendar/Correspondence 样本仍不支持。

例如 P={A,B}、全来源 A=100/C=300、收入显式 sum(empty="zero") 空/全 NA 为零：
`revenue.per(P).mean()` 得 50；`revenue.per(CustomerId).mean()` 得 200。
per(P) 的域外 C 不进入内部求值；observe(by=P) 则仍要求先限制贡献，否则输出缺 C 应失败。
该反例决定两种绑定记录不能合并。

### 7.3 配对与时间样本规约

SourceWeightedMean 在同一贡献根形成共同有效配对，乘积和、权重和、支持数使用同一掩码。
PerWeightedMean 先在相同 `(k,j)` 完成 value/weight 两个 Metric，再共同配对；
其样本域包含两侧相关键的并集或全部显式域键。二者共享配对数值规则，不共享输入阶段或充分性结论。
归一化权重不要求业务可加授权，也不因此获得 share 的贡献证明。

状态 P95 的执行次序是“同一采样时点空间 sum → 观察期间内 quantile”。
两设备 A=[10,0]、B=[0,10] 的同时点总体 P95=10，各设备 P95 相加为 19。
绑定图须保存固定状态轴的要求直到整个 Metric 绑定完成；不能在中间 sum 构造时将要求丢弃，
也不能用两个完成 P95 或失去时点的 bag 做空间 rollup。


## 8. 语义规则、物理资格和编译器

### 8.1 语义规则与物理资格各有一个 owner

语义表达式拥有贡献、单位、时间/样本政策；来源准备拥有真实 schema、连接与后端方法资格；
compiler 拥有需求、stage/参数和检查时限；本地方法拥有完成数据数值语义。
构造不提前读取未知 schema。每个字段使用位置先独立检查，再共享物理列。

### 8.2 三种规约不能因名字相同而共享完整实现合同

来源规约消费贡献；per 先完成每个内部样本公式；Frame.reduce 消费当前行。
完成均值或 distinct 计数不能代替原贡献/集合。行均值 55、原整体 1000/91 与 distinct 4/3
反例必须保留；没有 Frame 原量 rollup/drill/share 的假等价实现。

### 8.3 编译流程

构造闭合输入图，准备显式来源，推导类型/完整轴/值/presence，规划一套拓扑 stage，
检查支持方法并保留全部严格义务；读取/评价后验证完整数据，再按消费者释放中间资源。
固定存储读通过 LocalFrameServices.read_complete，不能递归 compute 或扫描生产历史。

### 8.4 compiler 与 optimizer 的职责交接

只基于真实 prepared owner/type/topology 做正向资格判断；AST 相等、Entity 名称或同一
定义不能证明同一次输入。source-only Population 关系/数值优化保持实际准入范围，
固定 Frame key 消费保持参数传输边界。多根先独立规约再组合，不能制造 fanout。

### 8.5 编译复用与运行结果缓存分开

重用定义/编译不复用业务答案。待执行来源再次求值可能变化；返回的完成 Frame 才固定数据。
不存在自动历史匹配、TTL、缓存回源、生产图恢复或新结果缓存管理 API。

## 9. 统一执行调度与执行内依赖

### 9.1 逐 stage 消费

同一个调度器处理来源、字面量、固定输入与本地计算。输入共享按消费者计数和真实身份，
自组合读取一次但保留两项逻辑消费。检查不能被输出剪裁提前释放。

### 9.2 名单和多来源的执行内交接

Frame 普通轴桥接联合去重后投递完整键，来源贡献可变化但固定名单不重新排名。
DuckDB 临时 Arrow relation、合格 PostgreSQL typed arrays/MySQL JSON 与其它准入键运输
继续检查 null-safe 完整元组、类型、原生容量及请求限制，不引入通用键数阈值或拆批合并聚合。

### 9.3 局部同输入保证

完成数据操作消费同一明确输入，不用相同公式冒充共同样本。独立来源根不承诺跨源事务快照。
源读时间、当前求值完成时间和保存提交时间分离；无对账/重试制造的共同快照。

### 9.4 统一 pandas 交接与内存边界

来源由 Ibis 返回 pandas，原生数值精度损失按实际后端合同接受；handoff 规范实际有效类型，
不能靠转回声明 dtype 恢复精度。完整键、NA、单位、时间仍严格。内存不足失败，不自动
spill、截断或回源。native sum/mean/weighted/ratio 有 PG/MySQL/Trino/ClickHouse 路线，
这不是当前 Frame pipeline 新的 Runtime 验收证明。

## 10. Query 组合与终端边界

本节保留旧链接锚点，当前公开 Query 已退役。Frame 操作是当前数据选择/算术/规约、
全轴 align/compare 及有限冻结日历 match。普通轴显式 on 必须完整，零轴/唯一日历轴
只有合同规定的推导；match 从不自动选择。outer 保留左序再右独有序，presence 独立于 NA。
配对输入再次对齐、任意身份关系对应、自动广播、分区 Top 和日历粗化延期。
原整体或新属性问题需显式新来源，固定数据不隐式取得原公式/历史能力。

## 11. 时间、窗口和实际覆盖检查

Metric.window 保持来源意义；每端点完成自己的 W(t)。Frame 中冻结目录解释历史数据，
match 要求完整相容名义分区与实际边界一致。先建立稳定部分一一目录映射，再检查实际联合行
exact/outer 覆盖；未用目录桶不产生行。损坏附件不是无对应端点。见[时间合同](timezone-and-calendar-design.md)。

## 12. 保存、恢复与读取出口

### 12.1 一次提交的内容

Store 12 保存一种 Frame 协议：精确所有权/result ID、闭合结构、类型附件/收据和一份
来源事实+当前求值审计。Run 只发布已有结果，不再次保存求值副本或 producer graph。

### 12.2 重开只读取产物

persisted_frame(str ID) 打开同 Session 提交元数据；完整读取验证必要数据与附件，
无需 Catalog、模型、schema/连接或时区规则。Artifact 从持久根取得且只读审计。

### 12.3 格式切换

旧 generation 全部数据读拒绝且旧字节不变，无迁移/双读。current/recent 缺当前代时可
None/空而不暴露旧数据；create/get_or_create 不得初始化旧项目旁路。兼容环境/原版本导出
恢复选择见[切换说明](analysis-frame-cutover.md)。

### 12.4 读取与 pandas

导出保留 raw-key MultiIndex、原行序、nullable 值与独立 presence；attrs 为隔离的轴描述、
单位、类型化冻结目录/映射表。attrs 不能作为导入合同，展示标签不变成新公开投影列。

### 12.5 统一读取：先打开描述，再按需扫描

n=0 与审计只读元数据；正 n 解码有序前缀和所需目录记录；full compute/export 验证完整
必要消费数据。hash/解压 I/O 不受 n 字节上限保证，预览成功不认证未读部分。

### 12.6 Parquet 布局、顺序与写入参数

沿用存储 owner 的 Parquet/分批 profile，实际物理行序跨 row group/batch 保持。
不新增独立排序清单、用户 codec 注册或跨产物共享文件/删除/压缩协议。

### 12.7 校验成本与提交可见性

只有完整有效结果进入现有发布事务。发布故障保留内存结果与旧提交；清理仅自身资源。
未知确认根据原 Run/journal 协调，不能重新求值；读取校验不改写原审计。

## 13. 目标代码组织与旧模块迁移

DSL/Session 是公共薄层；core 拥有闭合结构、精确输入、冻结解释和审计；compiler 与
materialization 共用原调度/来源执行；frame_services/terminal_* 拥有本地读写事务；
pandas_export 拥有隔离导出。按实际 producer/consumer 删除退役 Query receiver 分支，
不按名字删除 source-original 数值 owner 或私有源计划。无兼容别名、迁移或第二协议。

## 14. 披露、错误与实施顺序

Help 拥有静态合同、.show 拥有捕获事实、错误拥有真实修复、技能拥有工作流判断。
canonical Help 为 analysis.analysis_frame / analysis.frame_axis。公开 43-name 快照、
签名/属性/CLI/错误/动态 ID 指引和 current/latest EN/ZH 同步；静态 Help 不执行 getter。
Root 的 S7/S8 集成和版本状态由实际验收决定，历史计划不是实施证据。

## 15. 架构验证与完成判据

A01-A24 由[实施计划](analysis-frame-implementation-plan.md)拥有：三载体/冷恢复、精确句柄、
行序/NA/presence、原量反例、冻结目录、零轴桥接、只读预览、事务故障与旧代字节不变。
开发自测、独立合同审查、业务 oracle、provider Runtime、slow 与 release 分别报告。
最终源码冻结后的不缩小 make check-agent 属于整体 S8；文档/设计不能替代未运行证据。
