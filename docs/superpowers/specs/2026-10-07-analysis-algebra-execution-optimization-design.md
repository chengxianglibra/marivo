# Analysis 代数执行与检查查询优化设计

Date: 2026-10-07

Status: A1 implementation and bounded acceptance are recorded in [A1 acceptance](2026-10-07-analysis-a1-acceptance.md). A2 fixed selection implementation and bounded acceptance are recorded in [A2 acceptance](2026-10-07-analysis-a2-acceptance.md). A3 state-kernel convergence and bounded direct-key L8 implementation are recorded in [A3 acceptance](2026-10-07-analysis-a3-acceptance.md). Trusting explicit semantic declarations and handling numerical overflow at execution are the user-selected directions.

Research baseline: `panda@6801154111811bd94cd17343d52056e72306103a` plus the working tree inspected on 2026-10-07. Concurrent numeric and documentation changes exist; the SHA does not describe all inspected files.

## 1. 目标与决定

让分析代数直接帮助执行器减少重复工作，优先交付两项优化：减少没有独立责任的检查查询，
以及合并 fixed 路线连续筛选中的扫描、索引构造和中间结果转换。随后收敛已有原始状态
归约内核，在现有调用确实出现分层归约时应用 L8。实施收益以实际查询、扫描、转换和
耗时衡量，不以逻辑节点减少或所有算子使用同一个函数衡量。

本设计采用以下决定：

1. 有明确语义声明的事实直接作为前提使用，不自动查询来源再次证明。声明的范围之外
   没有额外保证；声明错误也不承诺一定被发现。
2. 算子构造能够保证的性质由封闭规则推导，不为每个中间结果重复扫描。
3. 没有上述依据的调用前提，按具体分析参数选择显式假设或实际检查。改变缺失、
   重叠等计算语义是另一种选择，不能伪装成关闭校验。
4. 必需的类型、参数、所有权、输入部件、物理载体、资源与发布检查继续由相应边界
   承担；数值溢出等异常由统一执行错误处理，不要求扫描来源证明所有重组都不会溢出。
5. 优化连续执行的物理安排，保留原逻辑图。原问题定义、节点身份、输入绑定、结果
   契约和可继续操作的能力仍由现有 graph、Signature、RuleDerivation 与 registry 拥有。
6. source 路线继续使用 Ibis 和已选择的后端；fixed 路线复用本地内核。领域算法继续
   使用其专用方法，不建立覆盖所有算子的通用解释器或代数搜索引擎。

A1 的第 5 节参数已进入可执行 API、owning specs、Help、错误修复、保存格式及中英文使用文档。A2 与 A3 已进入 fixed 执行器，保留原逻辑图；各自验收记录拥有实际范围与证据。

## 2. 当前实现与收益来源

当前代数已经参与语义推导和方法准入。`MethodNode` 构造调用 registry 推导，方法选择
消费前提、RequiredParts、类型与路线；分析代数不是可以整体删除的说明层。但公共算子
并非全部由六个元算子解释执行，领域方法保留专用推导和数值实现。

本次源码检查确认以下问题与既有能力：

| 位置 | 当前行为 | 有价值的改变 |
| --- | --- | --- |
| [rules.py](../../../marivo/analysis/core/rules.py) 的 `entity_members` | 声明主键之外仍创建 `source.unique_key` 义务 | 将具名 Entity 身份与版本粒度声明接入可用前提 |
| [model.py](../../../marivo/analysis/core/model.py) 的 `Evidence` | `declaration` 仅允许 `declared_key`、`field_ownership` | 允许 owner 已明确授权信任的来源性质，保留范围与依据 |
| [graph_lowering.py](../../../marivo/analysis/compiler/graph_lowering.py) 的 `lower` | 普遍为 native 阶段追加完整键和四态 Cell 查询 | 从声明、推导和真正未满足的调用前提产生检查，不按阶段追加模板检查 |
| [graph_source_execution.py](../../../marivo/analysis/materialization/graph_source_execution.py) | 检查结果独立读取；纯 native 计划已直接读取最终表达式 | 首先减少检查往返；继续复用后端优化，不再为了融合制造中间 source 表 |
| [graph_local_execution.py](../../../marivo/analysis/materialization/graph_local_execution.py) 的 `_transport_stage` | 每阶段转换行、构建键索引和 mask、筛选 parts，再构造 ExchangeResult | 在连续筛选中复用索引与行位置，只构造被实际消费的结果 |
| [local_laws.py](../../../marivo/analysis/core/local_laws.py) | L1/L7/L8/L9 辅助函数仅有测试调用；`StateEquation` 只表示状态等式 | 在具体执行路径使用规律的准入条件，不能直接把辅助函数当优化器 |
| [numeric_state.py](../../../marivo/analysis/methods/numeric_state.py) | 已有 `checked_sum`、`finish_division`、`merge_original` | 优先复用和收敛现有内核，不另造聚合协议 |

本次讨论中的临时内存探针提供了方向性证据，未归档成可复现的性能基线：

- 一个 5 行 DuckDB native 筛选图，合并两个选择后，检查查询由 10 次降为 8 次，
  主结果仍只读取一次，编译 SQL 长度和输出相同。减少逻辑阶段并未进一步简化该 SQL。
- 20,000 行 int64 fixed 内核探针，首筛保留 90% 时两阶段约 134.8 ms、合取约 96.2 ms；
  首筛保留 20% 时分别约 79.4 ms、93.0 ms。合取提前计算了更多第二谓词，反而更慢。
- 这些探针不包含完整 Store、发布和真实远程执行，也不代表当前并行修改后的性能。
  实施验收需要重新采集基线，不能把这些时间写成加速保证。

由此确定优先级：A1 前提与检查收敛最高，A2 fixed 连续筛选其次，A3 原始状态内核与
有条件的 L8 融合随后。L7/L9 暂不增加自动改写，不为适配规律扩张公共 DSL。

## 3. 前提与校验的责任划分

### 3.1 哪些放在语义层，哪些属于分析调用

判断依据是事实的业务 owner 和适用范围，不是查询成本高低。

| 事实 | owner 与依据 | 分析执行行为 |
| --- | --- | --- |
| Entity 完整主键、source grain、版本行唯一性与有效期约束 | 语义层 Entity 与 versioning 声明 | 信任，不扫描主键、版本重叠或所选版本的唯一性 |
| 字段 owner、关系端点、完整 join key 与结构基数 | 语义层声明及静态推导 | 信任声明；静态检查 refs、方向、键类型与版本选择 |
| 声明的类型、nullable、值域、数值约束与源时间解释 | 可复用业务性质由语义层拥有 | 已声明的性质不扫描验证；实际物理类型与载体仍在绑定和解码时处理 |
| 所有订单长期保证存在客户等匹配完备性 | 若为可复用业务保证，应由语义层拥有有方向、有范围的声明 | 精确命中声明时信任；当前 Relationship 尚没有该声明字段，不能凭基数推出匹配存在 |
| 某一业务时间范围的数据完备性 | 可复用保证归语义层；本次观察范围的假设归分析调用 | 信任适用范围内的声明；保留当前 `complete_during` 的调用方假设含义 |
| 两个本次结果键集合相等 | 分析配对调用；共同捕获可静态推导，否则由调用方假设或检查 | 不从相同行数、相同 Entity 或相同列名推断 |
| 本次归约的贡献互斥、目标覆盖、allocation 绑定 | 分析贡献模型与算子推导；仍未知的关系性质才需要调用依据 | 不用全局 `rollup_safe`、`complete=True` 代替具体事实 |
| 筛选保留唯一键、group-by 生成唯一组、Cell 构造合法、目标网格保留空组 | 算子构造与封闭规则 | 由推导获得，不追加中间结果查询 |
| 参数、单位、ref、owner、必须存在的状态分量和选定实现 | Analysis 静态准入及执行边界 | 必须检查；用户不能用假设跳过 |
| 解码、数值异常、读取完成、取消、截止时间、资源释放、原子发布 | Datasource、执行内核、Runtime、Store | 必须按实际执行处理；通常不需要另发业务查询 |

没有合适声明能力的长期业务性质，应补到自然语义 owner；不要用 Analysis 全局开关
代替它。首批不批量增加 nullable、finite、匹配完备性或时间完备性的通用声明框架：
已有声明先正确消费，新增声明只随真实消费需求给出精确契约。

业务完备性不等于读取完毕。即使读取了所有返回行，也不能证明业务事件没有漏入来源。
`complete_during` 也不能补齐缺少的状态分量、修复机器解码或自动赋予新的 rollup 能力。

### 3.2 当前检查种类如何处理

同一个 CheckId 可能用于不同前提，应按其绑定的 Fact 分类，不能按名称统一删除。

| 当前检查 | 目标处理 |
| --- | --- |
| `source.unique_key` | Entity 声明与版本粒度直接给出依据；筛选、投影、分组按规则运输或生成性质。停止自动源身份查询 |
| `source.single_value` | 字段 owner、适用版本与 one-side 结构可推导时直接使用；未知匹配范围仍由调用的显式假设或检查解决 |
| `source.exact_pairing` | 共同输入和键域可推导则免查；其余由 `ExactKeys` 的前提策略决定 |
| `source.group_mapping` | 区分 total、single-valued、injective；关系基数不能证明 total，非单射也不能用于宣称贡献互斥 |
| `source.contribution_partition` | 构造保证每个贡献进入一个目标时由规则给出；允许重复、重叠或 allocation 的方法按自身定义处理，不额外审计为“互斥” |
| `source.complete_coverage` | 拆清业务完备性、实际获取范围、目标网格和状态覆盖的含义；分别由声明、读取完成、构造及必要输入处理 |
| `source.calendar_members`、`source.calendar_contributions` | 日历与目标网格可构造的部分直接推导；实际输入范围与贡献对应未知时才检查，不能以旧日历证据证明新 source 读取 |
| `source.cell_policy` | 编译器生成的四态编码由构造保证；消费 Defined 的政策由内核或表达式执行，不通过 SQL 的默认忽略 NULL 替代 |
| `source.finite_numeric` | 已有声明免查；数值消费仍遵循选定方法的非有限值与错误政策，合并到实际计算或获取的数据中处理 |
| 无类型前提的通用 `IntegrityCheck` | 逐项归属到声明、推导、调用前提或必要消费契约，删除没有独立责任的阶段模板 |
| `TemporalCheck` | 停止为明确声明的源时间格式、时区或有效期自动扫描；仍保留声明解析、参数和实现资格检查 |

实现上，现有 `complete_coverage` 前提只用于对应原量和目标域的数据/状态覆盖；业务
完备性沿用 `CoveragePart.business_windows` 与其声明 owner，读取完成由 Runtime 的
读取状态承担。它们不能互相满足同一个前提。若现有绑定无法区分两个实际范围，先
补齐该事实的精确输入和范围，再删除检查，不能仅换一个 Evidence 标签。

对需要确实检查的 source 前提，优先使用已获取且正要消费的数据。原生计算无法从现有
输出判断的关系性质，可以保留独立检查查询。统一规则不是“所有检查都不得查询”。

### 3.3 必须处理的错误，不等于必须重新扫描声明

以下边界不可通过分析校验参数关闭：

- 跨 Session 输入、无效 refs/单位/参数、方法与路线不匹配，以及需要的 retained parts
  缺失或与本次输入绑定不符；这些尽量在业务读取之前拒绝。
- 实际需要读取的机器载体无法解码、缺少必需列、不可接受的物理类型、查询失败或
  读取未完成；不靠业务声明把未得到的数据变成已得到。
- 消费者建立唯一键索引时已遇到冲突，或按必需映射取值时已遇到不允许的缺失。
  不得覆盖旧值、取第一行或静默去重。无需为了“可能发现”另做全表或全结果检查。
- 实际数值计算或结果转换发生的溢出、不可接受的非有限值及其他运算异常。按既定
  错误或 Cell 政策处理；不将异常值发布成正常 Defined 数值。
- Runtime 的执行身份、截止时间、取消、资源释放与原子发布要求。

最终结果的 schema 和必需表示必须可供消费者使用。由可信构造保证的完整键、Cell 和
parts 性质不再逐个重复验证；实际消费中的结构错误仍失败。Store 8 已信任的本地提交
结果不重新审计、重算或恢复内容完整性证明。需要索引就正常构建，遇到冲突就报错，
而不是先扫描一遍验证主键，再扫描一遍构建索引。

## 4. A1：从前提产生检查，消除重复查询

### 4.1 前提解析

复用 `Fact`、`FactInput`、`Evidence`、`Signature`、`Obligation` 和 `RuleDerivation`。
依据保留四种来源：语义声明、算子构造/推导、调用方假设、实际检查。它们均能在
owner 允许的范围内支持准入，但只有最后一种表示实际完成了检查。

对每个方法必需前提，按以下流程处理：

1. 从输入和本次调用中寻找精确适用的语义声明、构造/推导或调用方假设。
2. 有适用依据，则前提可用，不创建检查义务。
3. 没有依据且该调用选择实际检查，则创建对应的 `Obligation`；所选实现必须具备
   履行方式。没有支持的检查实现时，拒绝准入。
4. 没有依据且该调用明确选择假设，则记录该前提的调用方假设，不生成查询。
5. 没有依据、没有允许的假设或检查路径，则给出结构化修复。不得自动改为其他
   配对、缺失处理、近似计算或执行路线。

依据的适用性沿用现有 binding、domain、quantity、FactInput 和定义版本，不创建
新的可信度等级、证明数据库或全局事实缓存。复合关系前提保留完整且有序的输入：
对 A/B 的相等键集合假设不适用于 A/C，对一个窗口的完备性也不适用于其他窗口。

`EvidenceBasis` 可增加一个内部 `assumption` 分支，用于调用参数产生的事实。
`declaration` 与构造依据的可用 FactKind 由具体 owner 放开；不能改成任意字符串都能
提交任何事实。`available_facts` 继续只返回当前精确绑定可使用的事实。

筛选运输输入的唯一性和它的假设依赖；group-by 的输出唯一性来自构造，不反向证明
输入没有重复。来源字段有有限值声明，也不推出其乘积或总和一定不溢出。

### 4.2 检查生成与执行

从 `entity_members`、绑定字段、观察与归约的推导入口修正依据，随后改 lowering：

- 停止无条件为每个 native 阶段创建 `_key_violations` 和 `_cell_violations` 查询。
- 特殊 lowerer 中的检查也要逐项归属，尤其是 field-owner coverage、fanout、版本和
  时间扫描；不能只删最后一段通用代码而留下相同检查。
- 内部结果运输复用已得性质；不在每次 `from_arrow` 时重做已由私有生产者保证的
  完整业务检查。外部物理读取的必要解码与本次消费契约由原边界承担。
- 只将实际检查义务交给 physical selection 和执行器；实现静态资格仍不是来源
  数据证据。每个必需检查必须成功完成，不能靠伪造 `CompletedCheck` 补齐清单。
- 相同实际输入、同一前提和同一履行位置的检查可以执行一次；不同 source 读取不
  共用检查结果。通过调度引用完成记录，保留每个消费节点对该记录的绑定。
- 已获得的数据可在本次消费中完成检查；额外查询只用于无法这样履行的未满足前提。

source 检查与计算目前允许独立读取不同版本。保留该边界：实际检查记录只描述自己的
读取，不能升级为后续查询的快照保证。减少检查不新增事务、source 上传或固定快照。

显式数据质量审计继续走已有 `catalog.source_health(..., checks=..., scope=...)`，
不能由普通分析隐式调用。审计不会修改语义声明、readiness 或自动豁免后续调用的前提。

### 4.3 数值异常与 L8

溢出属于实现和执行错误政策，不影响 L8 在原始状态上的数学结合律。使用 L8 不需要
提前证明所有可能的中间组都装得进存储类型，也不为此生成来源扫描。

数值执行复用统一的异常转换和既定 Cell 政策。零分母、空组和 Unknown 按方法含义
处理；不得将全部异常笼统转换为 NULL。溢出错误应指出实际消费者、运算和载体，并
清理本次资源，不尝试换后端或偷偷改走更宽数值路线。

浮点重分组可能在成功执行时产生不同舍入；这由所选方法和物理实现的数值契约约束。
与并行中的数值类型推导方案共同使用一个 owner，不恢复另一套强制精确算法。
整数/精确状态在可表示范围内按精确等式验证；允许的原生精度差异按方法契约验证，
不能把任意差异称为正常误差。
不承诺不同物理分组具有完全相同的中间溢出位置或成功/失败集合。

某方法若要求输入满足未被声明或推导的数值前提，而实际输出不足以判断该前提，必须
将检查合并到受支持的计算表达式，或保留必要检查。不能只检查最终数值是有限的，便
声称所有输入均已检查；也不能通过删除检查隐式放宽该方法的输入政策。

## 5. 拟议的调用参数与缺省行为

只给当前真实存在的未知前提增加参数，不提供全局 `validate=False`，不暴露内部
CheckId、Fact 列表或通用 assumption 对象。

A1 的首批增量如下：

| 公共位置 | 参数 | 精确含义与缺省 |
| --- | --- | --- |
| `ExactKeys` | `verification: Literal["check", "assume"] = "check"` | 只解决当前输入键集合相等这个未满足前提。已有共同域依据时免查，否则缺省检查；assume 记录本次调用方假设 |
| `LogicalAnalysisDomain.read` 的相关公开变体 | `match_verification: Literal["check", "assume"] = "check"` | 只解决所选成员、路径与精确版本的必需 field-owner 匹配。自身 owner 或适用保证可推导时免查；未知时缺省检查 |

参数里的 `check` 表示未满足前提的处理方式，不能要求重新验证已有语义声明。
强制审计来源声明使用独立 `source_health`。选择 `assume` 时不产生验证查询，也不
声称已检查；假设为假时不保证业务结果正确或违约一定被发现。

例如拟议调用 `pairing=mv.ExactKeys(verification="assume")` 仍要求 ExactKeys 的相等
键域语义。它不能被解释成允许只保留交集。遇到不可避免的消费错误仍失败，但不能为
“补充保障”再偷偷运行一次集合检查。`UnionKeys(missing=...)` 继续拥有另一种计算
语义；关闭验证不会自动选择它。

`read` 的参数也不定义允许缺失时返回什么；若产品需要允许缺失，必须另由该操作
明确区分缺失匹配与已有行的 Null，并给出完整结果语义，不能借此参数混入。

普通原始 rollup 的贡献分区和状态覆盖优先由观察、分配与运输规则给出，不新增一个
可跳过所有状态要求的 `rollup(check=False)`。当前没有公共入口消费的未知 Fact，也不
为它预建策略参数。相关专用消费者若仍需要未知关系检查，在其现有方法契约中履行；
新增假设选择只随具体公共需求交付。

`complete_during` 继续是明确范围的业务假设。source 查询不能验证“业务没有漏记”，
因此不添加 `check_complete_during` 开关。声明范围不覆盖观察时按既定 Unknown 政策处理。

这些参数只改变前提的获取方式，必须保存到调用定义及执行所需元数据中；不得将默认
check 结果与 assume 结果共用执行缓存。现有 API 缺省尽量保留未声明前提的检查行为，
而已有声明与推导的前提不再额外检查，这是明确的信任边界调整。

## 6. A2：fixed 连续筛选的物理融合

### 6.1 融合对象与准入

保留逻辑 DAG 和已选阶段，在 `validate_fixed_schedule` 之后由 fixed 执行器识别可连续
执行的阶段组。阶段组只是本次调用内的轻量安排，不替换 MethodNode，不保存第二份
语义图，不调用当前会创建新节点的 `fuse_selection` 改写原图。

首批限定于现有 L1 已覆盖的 int64 普通数值选择：

- 相邻的 `PartsTransport(mode="where")`，同一 fixed 输入链，量、角色、retained parts
  与 unknown 政策满足现有方法条件。
- 中间输出只有下一筛选使用，且不是本次请求的结果；共享分支和其他可观察输出是
  融合边界。不跨显式 materialized leaf 或执行请求边界。
- 首批不含外部 predicate 输入、`is_defined`、cohort、limit、rank/display、归因视图、
  业务覆盖变更和领域专用 transport。未覆盖的合法计划正常执行原有阶段。
- 原方法分别满足 registry 的准入与物理资格；L1 条件由既有语义 owner 提供，不能
  因为看起来只有两个 filter 就忽略 parts、作用域或错误政策。

后续类型或谓词扩大需有真实调用与行为验收；本阶段不修改现有 int64 L1 的资格来
宣称 float、Decimal 或所有标签政策已经支持。

### 6.2 执行方式

采用顺序筛选与延迟构造中间结果：

1. 初始输入只构造一次必要的完整键索引、行访问表示和保留部件定位。
2. 第一阶段在原输入上计算选择，得到存活行位置。
3. 下一阶段只访问前阶段存活行，继续按原阶段顺序求值和处理错误，不把所有谓词
   提前放到原始全量输入上，也不改变单个谓词树原有的求值规则。
4. 中间阶段保留私有行位置和必要的轻量结果信息，不执行一轮完整 `from_arrow`、
   `to_pylist`、索引重建和 parts 复制。
5. 到实际消费边界才筛选 primary 与需限制的 parts，按原终端节点的契约产生结果。

mask 可以复用，parts 不能统一处理。应限制的行部件沿相同键像限制，固定参照、端点
和专用保留部件按现有 part transformation 处理；存在不了解的部件运输时不融合。
中间节点有消费者时必须产生其结果，不能为了少一次转换改变共享节点的实现语义。

即使第一筛选极具选择性，第二谓词也只在存活域上求值。这样同时降低转换次数与保留
原求值工作量，避免临时探针中合取融合反而增加成本的问题。

### 6.3 身份与错误

同一逻辑计划开启或关闭 A2，definition fingerprint、逻辑节点身份、问题定义和结果
契约相同。物理执行组不参与语义 fingerprint；现有 plan digest 若描述实际物理安排，
应按其 owner 保存正确实现信息，不假装不同执行器布局完全相同。

实际谓词错误仍指向原阶段，不能只报告“融合组失败”。候选不满足融合条件时继续
使用已经选择的正常实现；已经开始融合计算后失败，不重试原路径。
fixed 仍不读取来源、不上传输入、不借 DuckDB 重新查询 Parquet。

## 7. A3：复用原始状态内核，有条件应用 L8

### 7.1 收敛实际重复代码

先让现有 sum、count、mean、ratio、weighted mean 原始状态消费者复用已经存在的
`numeric_state` 实现。共用的内容是状态分量合并、空状态、数值错误处理与 finish；
各方法的状态布局、零/空值政策、权重、单位和 quantity 绑定仍由原方法拥有。
不把 current-row summarize 改成原始量 rollup，也不从展示值恢复丢失状态。

source 表达式继续使用标准 Ibis 和已选 native 数值路线。语义规则、所需部件和数值
政策可以共用，本地 Python 合并器不接管数据库执行。领域扫描、journey、history、
funnel、retention、attribution、association 和 forecast 不为“统一”改写成六元算子。

这一步的独立价值是减少多处手写 sum/count、状态判定与异常转换的分歧；只有
确实存在重复实现的消费者纳入。仅改调用层名字、未减少重复职责的整理不算交付。

### 7.2 分层归约融合

当现有图已经表达相邻的原始状态归约时，L8 允许对同一批贡献直接合并到最终目标：

```text
Merge_h(Merge_g(state)) = Merge_(h composed with g)(state)
```

应用条件必须同时满足：同一原量、贡献与分配；映射全定义且角色链不丢失；完整可合并
状态；同一最终目标组域与空组政策；同一 finish；终端需要的部件、绑定和假设可保留。
中间结果被请求、被其他分支使用、改变参照/权重、涉及有序 fold，或部件运输无法
表达时，继续原执行。不能只凭 `StateEquation.left == right` 就扩大续算能力。

符合条件时只在终端 finish，省掉未被消费的中间展示值与 ExchangeResult 构造。
存在显式 materialized 中间结果时，它是固定输入边界，不能越过它从历史贡献重算。
数值异常按第 4.3 节处理，不用额外 source 查询证明 L8 的结合律。

公共路径 `fixed.group_by(customer).rollup().rollup()` 已确认可以表达相邻归约。
首批实现仅融合 sum、sum_zero、count、mean、ratio、weighted mean 和 linear 的
完整输入键投影，后续坐标只能减少；parts 限于 Subject、original_state 和 coverage。
共享节点、请求边界、显式物化、待完成检查、时间映射、嵌套贡献坐标、allocation_state
和其他部件均结束融合组。资格不足继续原路径，开始融合后的失败直接传播。
不新增 DSL，不扩张现有类型或原生数值路线；独立行为与成本证据见 A3 验收记录。

### 7.3 L7 与 L9 的边界

L7 可减少真实多段固定映射的索引工作，但当前辅助函数不是生产路径；尚无收益证据时
不建立映射搜索或预生成全域合成表。之后有真实重复映射消费再作独立优化。

L9 只适用于保留目标组及其完整原像的限制。聚合值筛选不能推到单条贡献，也不能删除
显式空目标组；计算 G' 所需的聚合不能被省略。当前公共选择能力没有表达该限制时，
不新增组键筛选 API 仅为应用 L9。两条规律继续作为准入边界和反例，不纳入首批自动优化。

## 8. 保存、披露与规范协调

A1 会改变前提依据与检查义务，不是所有错误时机都保持等价的内部整理。必须同步修改
相应方法定义、导出的签名和错误说明，不能只关闭 SQL 后让计划继续声称完成了检查。
声明/假设未扫描、实际检查范围、数值政策分别如实保存在现有元数据中；不建立新的
公开证据令牌或重验 API。

A2/A3 的物理安排不改逻辑定义与原结果契约。若参数、Evidence 或保存结构确实改变，
由现有 graph/descriptor/continuation owner 决定最小必要版本变化；不为纯阶段分组
升级 Store，不添加兼容别名、旧格式双读或迁移层。旧格式若需拒绝，保留其字节并给出
明确修复，不自动修改旧 Artifact。

实施时需要协调的规范 owner：

| owner | 必需更新 |
| --- | --- |
| [Semantic loading and validation](../../specs/semantic/loading-validation-introspection.md) | 声明可信范围与 Analysis 交接；匹配完备性不能从基数推出 |
| [Semantic object model](../../specs/semantic/semantic-object-model.md) | 实际新增的可复用声明；不加入执行检查开关 |
| [Python Analysis design](../../specs/analysis/python-analysis-design.md) | 前提获取、检查调度、物理分组和 source 独立读取边界 |
| [Operators and frames](../../specs/analysis/operators-and-frames.md) | 数值/Cell、原始状态、parts 与拟议配对参数 |
| [Session state and runtime](../../specs/analysis/session-state-and-runtime.md) | 实际完成记录、执行键、保存与失败发布；继续遵守 Store 8 本地信任 |
| [Analysis algebra theory](2026-09-23-analysis-algebra-theory.md) | 信任声明、检查义务与数值执行边界的表述一致 |
| [Analysis/Semantic simplification](2026-10-07-analysis-semantic-simplification-design.md) | 对来源性质及检查责任的旧表述按本设计的分类修订，保留历史实施记录 |

改变公共参数时同时更新 native Help、动态 contract/show、结构化修复、快照与预算测试、
当前中英文 site 示例和 CLI 的受影响部分。packaged skills 需要修改时仍须单独获得
明确授权；本文不修改 AGENTS.md 或任何 skill，也不以 skill 更新作为内部优化的前提。

## 9. 验证与收益验收

### 9.1 独立行为验收

测试按风险组织，使用公共路径和独立预期值，不把“调用了新函数”当正确性证据。

| 行为 | 必须证明 |
| --- | --- |
| 信任声明 | 使用声明主键、版本粒度和可推导 owner 的普通查询不提交对应验证查询；预期值来自独立数据 oracle |
| 依据范围 | 两输入关系前提保留有序输入；换窗口、换对象或换版本不能复用原假设；构造输出性质不反推来源性质 |
| 显式假设/检查 | 同一未知键集合在 check 下发现不匹配并失败，在 assume 下无验证查询且标识为假设；共同域可推导时不查询 |
| 必需消费错误 | 无效参数、跨 Session、缺少状态/列、实际解码错误、索引插入冲突和数值异常不能因 assume 成功 |
| 筛选融合 | 独立预期域、数值、Cell、parts、空结果与续算契约一致；高选择性首筛不增加第二谓词求值行数 |
| 融合边界 | 共享中间节点、外部谓词、改变消费域的标签选择、固定参照及领域部件按原路径执行 |
| 原始状态 | 不均匀组大小的 mean、ratio、weighted mean 使用完整状态；空目标组保留；当前行均值不替代原量归约 |
| L8 | 合格的已有图在最终状态与契约上满足规律；丢失分量、不同权重/参照或有序 fold 不融合 |
| Runtime | 查询与本地异常不发布半成品；截止时间、取消、清理与既定路线不变；fixed 无 source 访问 |
| 恢复 | 本地提交结果不重新审计；假设不会被读回成 CompletedCheck；保存结构改变时按现有版本边界明确处理 |

source 声明故意违约的测试不要求普通查询必须发现违约；只有实际消费者遇到的结构错误
才要求失败。数据审计能力使用自己的显式范围与检查测试，不混入普通分析验收。

### 9.2 成本基线与出口

使用同一构图、同一固定输入和同一数值实现比较启用/关闭优化的执行。A1 另有信任契约
调整，应分开报告删除了什么检查；不能把 A1 和数值方案混跑后归因给 A2。

| 工作包 | 可验证的成本出口 |
| --- | --- |
| A1 | 普通声明/推导充分的 native 计划不发身份、Cell 和相同单值模板查询；剩余每条查询都对应实际未满足前提。terminal/parts 读取数量和读取完成责任明确 |
| A2 | 合格连续筛选只构建一次必要行/键索引，只在消费边界构造结果与限制 parts；后续谓词求值行数与顺序执行相同 |
| A3 内核 | 纳入的消费者删除重复的状态合并/finish/异常职责，使用一个 owner；行为和数值路线验收通过 |
| A3 L8 | 合格真实调用减少中间 finish、分组或结果转换；保留完整终端状态和 parts。无合格调用不宣称加速 |

性能样例至少覆盖短/长筛选链、首筛低/高选择性、无 parts/有多个 retained parts、空结果
和共享分支。计时、内存及确定性工作次数分别采集，记录环境、输入、路线与多次重复
分布；不以小数据微基准的一次时间建立硬性百分比承诺。

首轮在 DuckDB source 和本地 fixed 采集成本与行为证据；共用 lowering 改动必须跑其他
已支持后端的相关受影响测试。不重复整个 backend × 算子 × 类型矩阵，也不将静态检查、
模拟表达式或 DuckDB 结果宣称为真实远程资格。

按仓库入口运行最窄行为测试和受影响 typing/lint，随后对共享行为运行 `make test` 与
`make check-agent`；实际 source 行为使用必要的 `make runtime-test TESTS=...`。
本设计不要求为普通开发启动完整 release Runtime 或运行发布流程。

## 10. 实施顺序

| 工作包 | 责任与修改范围 | 完成条件 |
| --- | --- | --- |
| A1a 声明与推导接入 | `core/model.py`、`core/rules.py` 与实际必要的语义交接；建立检查责任清单 | Entity 与已有语义性质可用；构造性质运输正确；前提缺失不会靠删检查被接受 |
| A1b 检查调度与消费 | `graph_lowering.py`、source/local execution、exchange 及相关专用检查产生者 | 删除通用阶段查询与重复消费扫描；真正的检查义务履行；mandatory 边界验收通过 |
| A1c 具名前提参数 | `_comparison.py`、`public_dsl.py`、构图/保存与公共披露 owner | 第 5 节两个拟议参数端到端一致；假设和完成检查区分；没有泛化的校验关闭口 |
| A2 连续筛选 | fixed execution 及必要的封闭资格判断 | 现有 int64 L1 范围正确融合，工作次数减少，高选择性路径不提前全量求值 |
| A3 状态内核与直接键 L8 | `numeric_state.py`、实际原始状态消费者与受限 fixed 阶段组 | 删除重复合并/finish；合格公共链减少中间工作；终端契约与必要失败边界通过独立验收 |

每包独立更新其 owning contracts 和验收，保留当前工作区无关修改。并行数值类型方案
先拥有 dtype、精度和原生算术的决定，本方案在其最终实现上复用，不把类型改动计为
代数优化，也不恢复已被用户放弃的精度要求。

本文保留原设计与实施边界。A1、A2、A3 的实现、实际验收及未验证范围由各自验收记录
拥有；不能由前序工作包的通过结果推导后续工作的资格或收益。
