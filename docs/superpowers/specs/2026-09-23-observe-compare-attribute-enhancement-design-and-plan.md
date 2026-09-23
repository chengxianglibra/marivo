# Observe / Compare / Attribute 组合能力增强设计与实施计划

Date: 2026-09-23

Status: proposed（设计与计划；本文不代表功能已实现或验收）

## 目标与依据

目标是让 Agent 用类型化的 Logical Dataset 表达一个可继续组合的分析问题，并在执行后读到该问题的目标、依据、数值核对及解释限制。保留现有三层：语义层 Metric 定义“算什么”，Logical Dataset 定义“对谁、在哪个范围、按什么规则分析”，Materialized Dataset 与 Evidence 记录“这次实际算到了什么”。不建立平行 AST，不把 Delta、Attribution、Estimate 等结果合并成含义模糊的 Metric，也不开放任意两个数值 Dataset 的算术组合。

本文综合[共享讨论](https://chatgpt.com/share/6ab34835-83d8-83e8-9a8d-95d939a7edfb)及后续对现行 API 的核对，聚焦 Metric 观察、Metric Delta 和其 Attribution；Event/Funnel 等其他变体继续由各自规范管理。现行权威仍是 [Observation Model](2026-09-01-lazy-analysis-observation-model-design.md)、[Typed Operators](2026-09-01-lazy-analysis-typed-operators-design.md)、[Materialization Runtime](2026-09-01-lazy-analysis-materialization-runtime-design.md)、[Python Analysis Design](../../specs/analysis/python-analysis-design.md) 和[语义对象模型](../../specs/semantic/semantic-object-model.md)。本文是这些规范的拟议增补；实施前应把获准的行为写回对应所有者，不能以本文覆盖现有已接受契约。

## 当前基础与真实缺口

| 能力 | 当前状态 | 待增强点 |
| --- | --- | --- |
| `observe` | Population、观察时间、Metric/RuntimeMetricExpr 与输出坐标分开；`with_dimensions()` 可在聚合前保留维度 | 在公开契约中让指标身份、成员口径、时间、坐标及聚合依据容易辨认；明确“计算所需粒度”与“输出粒度” |
| Metric `compare` | 只接受相同的规范化指标定义和兼容口径/坐标，固定 `current - baseline`，按注册的时间桶规则配对 | `Delta.contract()` 清楚展示两侧范围、方向、配对和缺失侧规则；报错指出具体不兼容项 |
| 维度 `attribute` | 已能在逻辑 Delta 上向两侧补缺失轴，保留原 Delta 并核对端点；物化缺轴拒绝回源 | 固定每类图节点的扩展准入、分区证明与拒绝修复；公开归因目标、范围、分母、方法及核对证据 |
| 外层切片 | 可在两侧 `observe` 时先保留 Region，再 `compare().attribute(axes=(Channel,))` | 现有 Attribution 没有 `split_by(Region)`；已构造的分析不能直接转换为每个 Region 独立重算的分析 |
| 计算组件归因 | `component_mix@v1` 用分子/分母私有状态计算**维度**贡献 | 尚无按指标计算图或 fold 状态输出“收入组件/订单数组件”对客单价变化贡献的结果变体；不能只覆盖 Catalog ratio |
| 单位级统计、参照归一化、推断 | 有受控 Metric fold、`rollup`、Attribution 份额与部分统计结果 | 缺少把单位级 Metric 值视为新样本的通用契约；参照集合与展示集合需明确；完整分析级不确定性需要独立设计 |

前两行和现有逻辑补轴属于**增强可读性与验证**，不是重新发明 `observe/compare/attribute`。`split_by` 和公式组件归因属于**新组合能力**。最后一行属于后续能力路线，不应混入本轮基础算子的签名。

## 全程不变的边界

1. 分析目标是已有、不可变的 Dataset 节点。下游可以请求更多辅助依据，但不能在原地把“全国收入变化”改成“各渠道各自的变化”。每个新结果有独立定义指纹、行契约和合法续接。
2. `observe` 的 Population 成员、观察时间、目标 Entity、输出坐标、分析单位以及计算所需的私有组件分别受权威契约约束。维度在某个节点可以是输出切片、比较轴、分解轴、归一化参照轴、分析单位或重采样单位；这不是 Dimension 的永久属性。
3. `compare` 只接受注册的、可证明同口径的输入。两侧的 Metric 身份、值类型/单位、聚合与近似方法、Population 选择、非时间范围、采样定义及坐标必须符合现行兼容规则。配对和缺失侧零值沿用现行契约，不能因为某一侧没有 Region 就擅自填零。
4. `attribute` 的贡献只解释一个明确 Delta 在一个明确范围内的**代数变化**。维度分解要求相应分区互斥/完整或有注册的精确分配证明；重叠标签不能靠最终数值碰巧相等取得资格。公式分解另有方法证明。跨期地区迁移的正负项也不自动表示“地区表现”或因果效应。
5. Materialized Dataset 是不可变输入。只有已保留结果/私有状态满足需求时才能续算；缺少维度、公式组件或重采样单位时在读取源之前拒绝，不沿 lineage 偷偷回源。跨 Session 显式使用 Artifact 仍依从现行 Store/所有权规则。
6. 用户已决定：**不做物理来源版本探测，不指定固定提交/事务版本读取，允许一次分析中的多次源读取看见不同状态**。这不取消语义层已有的业务时间快照、有效期及 Population 参照选择契约。不建立共享源快照、首尾版本标记、来源一致性等级、变化检测状态或自动重试。必要的成员、端点、分区和数值校验仍须执行；校验失败照常报错，但不因“发生过更新”这一事实单独拒绝。数值守恒只说明本次读到的值满足指定等式，不证明共同源快照、数据新鲜度或因果解释。

## 目标、依据和展示的统一契约

每个算子构造时确定以下事实，执行时补上只可能从数据获得的事实：

| 层次 | `observe` | `compare` | `attribute` |
| --- | --- | --- | --- |
| 目标 | Metric 或 RuntimeMetricExpr、Population、观察窗口、输出坐标 | 同一规范化指标定义的两侧观察及 `current - baseline` | 原 Delta、分解范围与分解依据 |
| 上游需求 | 所需贡献及保留状态 | 相同口径、配对键和时间桶 | 目标端点以外的维度分区或公式组件 |
| 构造时证明 | 语义绑定、单位、粒度与可达路径 | 类型/成员/坐标/对齐兼容 | 方法、分区/组件准入、合法图改写 |
| 执行时验证 | 输入与 fold 的现行校验 | 缺失、空值、数值类型及端点 | 分区覆盖、两侧组件、逐范围重组误差 |
| 对外解释 | 一行代表什么 | 谁减谁、如何配对 | 贡献参照哪个 Delta、占比的分母和限制 |

`Delta.contract()` 增加有界、可机器读取的两侧 Metric/Population/时间范围、方向、对齐规则、坐标与缺失侧政策。`Attribution.contract()` 和物化 `.show()` 增加目标 Delta、scope/axes 的角色、注册方法、`top_k` 的参照范围、完整性、最大核对误差与近似状态。只展示已存在且已核验的事实；展示层不再推断语义，也不把源一致性写成已证明。`component_mix@v1` 的 `current_value`/`baseline_value` 应明确为分配给该分区的**整体指标侧项**，不是该分区自己的局部比率。

公开导航由原生 Help、结果 `contract()`/`show()` 与结构化错误各司其职。冷进程中未先调用 Help 时，`contract()` 仍应给出可复制的合法调用/Help 路径；不兼容比较、重叠轴、不合法图改写、组件不可得等错误必须指出期望、收到的具体对象和根据实时契约可行的下一步。公开字段、方法、结果变体或错误变化都按一次披露契约变更处理。

## 逻辑需求传播与改写

现有 `attribute` 补轴已能沿逻辑 Delta 找到两侧 Metric，在目标之外建立辅助分区，并把扩展端点与原 Delta 核对。增强重点是把允许的路径写成封闭、可测试的算子规则，而不是引入任意图优化器。任一节点无法证明改写等价时，应在构造期拒绝；行依赖条件若只能执行时判断，则保留为类型化 action-time 校验。

| 图节点/操作 | 所需规则 |
| --- | --- |
| `observe`、`with_dimensions` | 从同一受治理 Dimension 路径取得维度；两侧保持相同成员、时间绑定与坐标顺序；私有贡献状态按 Metric 契约取得 |
| 聚合/`rollup` | 明确是重算同一 Metric，还是凭保留组件恢复同一 Metric；仅在对应 fold、分区及时间末端证明下改变坐标，绝不对局部 ratio 直接求均值 |
| 聚合前 `where` | 原成员选择必须可原位重建；不能为了补轴把筛选移到聚合后 |
| 聚合后 `where`、`rank`、`limit` | 保持原先执行位置及选择口径；若新增切片会改变选择集合或排序范围，而没有指定逐切片语义，则拒绝改写 |
| `compare` | 两侧同步扩展并重新做完整兼容/时间配对；选中行、缺失侧、采样与各自的选择摘要仍须对齐 |
| 维度 `attribute`、Top-K | 原 Delta 保持独立目标；辅助分区必须覆盖同一选中成员。Top-K 在每个新目标范围/父层级内重算，并以类型化 Other 表示剩余，不用展示后的前 K 行替代完整参照集合 |
| 组件 `attribute` | 原 Delta 保持独立目标；按指标根角色/状态索取两侧组件，复现根端点并核对贡献；不把组件当作 Dimension 补轴 |
| Materialized 扫描叶 | 只能消费叶内保留的坐标与状态；缺失则给出重建逻辑输入的修复，不查询原始源 |

此矩阵同时约束目标集合、计算参照集合、展示行集合。`where`/Top-K/`limit` 的顺序可能改变分母、成员和结论；不能只比较最终表的列名或总和。

## 新能力一：`split_by(Region)`

初始公开入口拟为 `LogicalAttributionDataset.split_by(dimension)`，返回新的 Logical Attribution。v1 只接收一个受治理的非时间 Dimension。为了满足结果家族的配对方法规则，`MaterializedAttributionDataset` 上也有同名方法，但 v1 一律于数据工作前结构化拒绝并指向重建逻辑分析，因为已发布的 Attribution 不能变成另一个目标。能力注册的动态准入只把逻辑输入列为可执行续接；物化 `contract()` 不得推荐一个必然失败的动作，Help 则说明其状态边界。本轮不增加 `Delta.split_by` 或通用 Dataset `split_by`。

以下 `Revenue`、`orders`、`PaidTime`、`Region`、`Channel` 为已有类型化语义对象。**当前**必须在两侧先保留 Region：

```python
current = session.observe(
    Revenue, population=orders, time_scope=august, time_dimension=PaidTime
).with_dimensions(Region).aggregate()
baseline = session.observe(
    Revenue, population=orders, time_scope=july, time_dimension=PaidTime
).with_dimensions(Region).aggregate()
regional = current.compare(baseline).attribute(axes=(Channel,))
```

**拟议**的组合写法是：

```python
nationwide = national_current.compare(national_baseline).attribute(by=(Channel,))
regional = nationwide.split_by(Region)  # proposed
```

`regional` 在每个 Region 内重新建立两侧观察、Region Delta、Channel 分解及其核对；Region 是输出目标范围，Channel 是分解轴。`nationwide` 完全不变。拟议的 `nationwide_delta.attribute(by=(Region, Channel), mode="joint")` 则以全国 Delta 为目标，把 Region×Channel 当联合分解轴；两者即使显示相同的维度列，也有不同的贡献分母和重组目标。此处保证的是逻辑定义的等价关系；若分别执行全国、地区及手工构造的分析，源在各次运行间变化时，结果数字不保证彼此核对。

`split_by` 的 v1 准入限于来源为同一 Session 内可追溯的逻辑 Metric Delta、两侧都可安全加入目标 Dimension、`mode="joint"`、原 Attribution 无后置 `where/rank/limit` 的图。两侧独立的聚合前选择可保留在原位置；聚合后选择、`rollup`、采样和层级轴在 v1 拒绝。`top_k` 若原归因已声明，须在每个 Region 的完整 Channel 集合上重新选择，并分别生成 Other。对于原图中已作为分解轴的 Dimension、已作为目标坐标的 Dimension、歧义/重叠路径、不能分区守恒或会改变全国选中集合的操作，给出具体拒绝与修复。若仅需筛选或排序已计算的地区×渠道贡献行，可在 `split_by` 后使用 `where`/`rank`；`limit` 仍是全局行截断，不承担每地区 Top-K。若要改变参与计算的渠道成员，须在两侧观察的正确位置声明选择并重建逻辑分析；不能用对归因结果行的筛选改变原目标或贡献分母。

每个新 Region 的 current/baseline 配对、单侧缺失、空集零值、跨期成员迁移、Top-K/Other、份额分母及数值核对都走现有注册语义。地区贡献可以超过地区净变化的 100%；净变化为零时相关份额仍是未定义。不存在“各地区结果天然可加回全国”的泛化承诺，尤其当分区重叠、选择口径或非加性方法不满足精确 fold 时。

## 新能力二：按指标计算组件归因

维度归因的行身份是业务 Dimension 成员；计算组件归因的行身份是**规范化指标计算图中的根级直接角色或已登记的 fold 状态角色**。Catalog Metric 与 RuntimeMetricExpr 都归一化为受控指标图，故按图的计算形态与两侧可恢复状态准入，不能按“预先声明的 Metric / 运行时表达式”划线。它解释当前目标范围内的同一个 Delta，不要求子组件各自形成可加的空间分区。

拟议公开接口统一为 `delta.attribute(by=...)`。`by` 是封闭的两种形态：`tuple[DimensionInput, ...] | list[DimensionInput]` 表示业务维度，`Literal["formula"]` 表示指标计算组件。它不需要单值的 `components="metric_definition"` 参数，也不增加 `attribute_formula()` 第二入口：

```python
delta.attribute(by=(Channel,))  # dimension attribution; proposed
delta.attribute(by="formula")  # metric component attribution; proposed
```

**现行**签名仍是 `attribute(axes=(...))`；上面是待接受的公开契约切换。接受后逻辑与物化 Delta、Metric 与 Event 的维度归因都统一用 `by=`，不留 `axes=` 兼容别名。`mode`、`top_k` 仅适用于维度形态；Event/Funnel 的 `target` 仅适用于 Event 维度形态。类型重载给出 Metric 维度、Metric 计算组件、Event 维度三种合法调用；运行时使用内部“参数未提供”哨兵区分省略与显式传值，所以即使传入默认值 `attribute(by="formula", mode="joint")` 也会拒绝，不以无关参数静默成功。原生 Help 保留现有 Metric `analysis.delta_dataset.attribute` 与 Event `analysis.funnel_delta_dataset.attribute` 两个按结果家族聚焦的目标；前者展示维度/组件合法变体，后者保留 Event 必填 `target` 与准入说明。当前 Help 从实现方法的 `inspect.signature` 取单个签名；切换时须由同一 Help 所有者展示并校验合法重载，不把内部哨兵或宽泛联合签名展示为完整公开契约，也不另建影子清单。Event 的 `by="formula"` 应在数据工作前结构化拒绝；仅当当前状态存在合法 target 与 Dimension 时才给出可执行的维度归因调用，否则说明缺少的前提并指向 Event Help。动态 `contract()` 只推荐本对象实际准入的续接。切片 0 落地 `by=` 切换后、切片 4 完成前存在一个过渡窗口：`by="formula"` 拼写类型合法但尚未实现，须以专用结构化错误拒绝并指向维度归因形态；切片 4 上线时该错误移除。

v1 的分解基底只取根节点的**有序直接子项**，或单一 `aggregate(agg="mean")` / `weighted_mean` 节点登记的两个 fold 状态；嵌套子图暂作为一个子项，不递归展平成所有叶，不声称做 Shapley 或因果分配。即使两个子项在规范化 DAG 中共用同一节点，也按提交时保留的角色/出现路径分别列行，不能只用规范化 node ID 作行身份。按根计算形态的封闭准入如下，Catalog 与 RuntimeMetricExpr 遵守同一规则：

| 根计算形态 | v1 计算组件基底与方法 | 准入边界 |
| --- | --- | --- |
| `linear` | 每个有序加/减项各一行；`linear_terms@v1` | 每个子项两侧值、系数与根值可恢复；嵌套 ratio 等子图仍是一个子项 |
| `ratio` | 分子、分母各一行；`symmetric_ratio@v1` | 子项两侧值可恢复、分母非零；子项本身不必可按维度加和 |
| `aggregate(agg="mean")` / `weighted_mean` | 分别用 `sum`/`non_null_count`、`weighted_numerator`/`weight_sum` 两个私有状态；沿用 `symmetric_ratio@v1` 的数值分配 | 两侧状态均已登记、可恢复且分母有效；行身份明确为计算状态，而非两个用户声明的 Metric |
| 原子 `aggregate(agg="sum"/"count")` | 无非平凡的公式组件 | 拒绝 `by="formula"`，建议直接阅读 Delta 或在满足各自维度准入时用 `by=(Dimension,)`；不输出一行“自身贡献” |
| `min`、`max`、`count_distinct`、`median`、`percentile`、表达式 body 等 | 无已登记的精确根级组件分配 | 拒绝并指出缺少的计算基底或状态；不以任意差值拆分冒充组件归因 |
| `slice` 包裹层 | 仅在过滤范围与根端点可证明保持一致时透明穿透到内层根 | 无法证明时拒绝；不得把不同过滤集合的子项放进同一公式 |
| 图中含 `cumulative` | 本轮不准入 | 现行语义规范的累计衍生比较许可不扩展到 attribution，包括以累计值作为 ratio/linear 子项的情形 |

所有准入行在两侧都必须有**已定义、有限的根值与每个直接子项/状态值**，且目标 Delta 有定义；ratio/mean/weighted mean 的两侧分母另须非零。执行时遇到 `null_input`、`delta=None`、非有限值或不能恢复的私有状态，v1 对该次计算结构化拒绝并指出出错坐标，不跳过或编造贡献。单侧缺失坐标即使因根指标的 `exact_empty_zero` 得到根零值，私有子项仍无已证明的另一侧值；v1 同样拒绝，不推测各子项空集值。以后若要支持这类行，须为每个子项登记空集构造规则，复现根零值并重新证明守恒。

线性根的第 `i` 个贡献为 `coefficient_i × (child_i,current - child_i,baseline)`，并核对所有项之和等于目标 Delta。`symmetric_ratio@v1` 对 `f(N,W)=N/W` 取两种替换顺序的平均；设 `c`/`b` 为 current/baseline，则：

```text
N_contribution = (N_c - N_b) * (1/W_b + 1/W_c) / 2
W_contribution = (N_c + N_b) * (1/W_c - 1/W_b) / 2
N_contribution + W_contribution = N_c/W_c - N_b/W_b
```

mean 和 weighted mean 将上述 `N/W` 分别替换为 `sum/non_null_count`、`weighted_numerator/weight_sum`。以收入 `1000 → 900` 万元、订单 `10 → 12` 万单为例，客单价 `100 → 75` 元，收入组件约 `-9.17` 元、订单数组件约 `-15.83` 元，总和 `-25` 元。每行分别记录角色/出现路径、两侧**组件原值及组件单位**、根指标结果单位的有符号贡献、份额及近似状态；不能把不同单位的组件原值伪装成同一列的同一单位。方法、分解基底、精度/舍入、端点及贡献重组误差作为结果事实冻结。该结果是所观测端点的代数分配，不暗示组件独立或因果效应。

逻辑 Delta 可向两侧观察索取已登记的子项值或私有状态；物化 Delta 仅在两侧已保留足够状态、根角色/出现路径、单位和方法依据时可继续，绝不重新读 Catalog 或源。现有物化记录是否保有这些事实须在实施时逐项审计并补齐，不能仅凭现有 fold 图宣称可用。每侧子项必须在相同目标范围内重构根端点，再与所归因的 Delta 在声明精度内核对；不满足就报具体端点/组件不一致，而不做物理源版本探测或重试。近似组件可以产生对近似端点的代数分配，但必须继承近似披露，不能声称精确。现有维度 `component_mix@v1` 与其行/字段语义保持独立；计算组件使用同一 Attribution 家族中的封闭行与证据变体。只有以后同一形态获准另一种业务含义不同的方法，才增加封闭的显式 `method` 选择。上述公开签名和结果变体须先在 Typed Operators 等所有者中接受。

## 后续能力路线及前置语义关口

这些需求来自同一“分析定义可组合”方向，但不应作为本次 `split_by` 或计算组件归因的隐含行为；每项须有独立所有者规范、公开变体和验收。

| 需求 | 必须先固定的契约 | 最小判别案例 |
| --- | --- | --- |
| 单位级 Metric 值再做统计 | 什么是单位、一单位如何形成一个 Metric 值、等权/加权、空值与缺失、单位样本和整体 Metric 的不同身份；不能扩大 `rollup` 为任意 `agg="mean"` | 用户 A `1/1`、B `0/99`：整体转化率 1%，用户转化率等权均值 50%，两者不得混同 |
| 参照集合与归一化 | 计算成员、参照成员与展示子集分别定义；数值归一化、业务贡献份额、经验分布各有不同组成/支持集证明；Top-K 前后归一化不同 | 展示 Top-10 不能把分母悄悄从全体改为 Top-10；净变化为零不输出虚假 0% |
| 完整分析级不确定性 | 统计单位、重采样单位、依赖结构、重复执行完整子分析、方法适用范围与资源上限；物化输入须有足够保留状态 | 两期 ratio 差值要在每次同一单位抽样后重做 `observe→compare`；不能直接把两个单侧区间端点相减 |

其中不确定性能力须分清估计不确定性与来源在运行中变化这两个问题。重复求值的样本必须来自同一次保留的输入实现；对不断变化的实时源重复查询，不能伪称对一个固定经验数据集完成重采样。保留输入实现不要求探测或指定源版本。本计划不通过 Bootstrap/Jackknife 推断源是否发生更新，也不以重采样替代来源快照。

## 实施顺序与验收

以下每个切片完成时都更新其权威规范、代码、原生 Help/动态契约、结构化错误、示例及 `site/src/content/docs/*/latest/` 中英文页面；不能只实现 Python 方法。打包的 `marivo-semantic`/`marivo-analysis` skills 若需修改，按仓库规则先取得**单独明确批准**，本次仅记录待对齐项，不改 skill。

预期主要触点是 `marivo/analysis/observation/` 的坐标/保留状态、`marivo/analysis/operators/` 的 compare/attribute/attribution 与补轴、`marivo/analysis/compiler/attribution.py` 的端点和重组校验、`marivo/analysis/materialization/` 的已提交证据展示，以及 `marivo/analysis/datasets/contract.py` 和 `_capabilities/` 的公开导航。相应测试覆盖现有 `tests/test_lazy_attribute_contracts.py`、数值测试和 disclosure journey；新变体的精确文件归属在切片 0 固定。

| 顺序 | 交付边界 | 关键验收 |
| --- | --- | --- |
| 0. 契约定稿 | 在 Observation/Typed Operators/Runtime 所有者中接受目标、轴角色、改写准入、状态相关 `split_by` 注册以及 `attribute(axes=...)` → `attribute(by=...)` 的一次公开契约切换；固定组件行/证据变体、错误、封闭方法表与原生 Help 的重载签名展示 | 给全国/地区、维度/组件、Catalog/RuntimeMetricExpr、逻辑/物化各组正反例作静态准入演算；无双入口、内部哨兵泄漏或被忽略参数 |
| 1. 目标与证据披露 | 增强 Delta/Attribution `contract()`、物化 `.show()`、字段说明、冷进程导航与结构化修复 | 新进程仅 `import marivo.analysis as mv` 即能看到可复制续接；现有 `component_mix` 侧项与各份额分母不被误读；不出现快照或因果保证 |
| 2. 逻辑改写准入 | 将现有补轴路径按上表显式校验，保留原 Delta 锚点、两侧端点、选中成员和位置敏感操作 | 所有保留接受的路径数值不变；新增拒绝仅限上表点名的位置敏感情形（如聚合后 `where/rank/limit` 上补轴），拒绝时给出重建修复；`where/rank/limit/rollup` 的允许/拒绝矩阵、重叠 Dimension、物化缺轴均有聚焦测试 |
| 3. `split_by` | 新逻辑图变换、每范围配对与归因、状态相关错误、Help/文档；调用示例使用已接受的 `by=` 签名 | 与手工两侧 `with_dimensions(Region)` 的独立预期值一致；地区目标与全国联合轴的 Delta/分母明确不同；原对象指纹与结果不变 |
| 4. 计算组件归因 | 实现线性项、对称 ratio、mean/weighted mean 状态基底，及专属行/证据变体、两侧组件保留/拒绝 | Catalog 与 RuntimeMetricExpr 各有手算正例；AOV 贡献重组 `-25`；带 scope 的组件归因逐 scope 独立重构端点并各有手算正例；原子指标、零分母、空值/单侧缺失、缺状态、同节点多次出现、单位、近似、物化与累计边界按契约处理 |
| 5. 独立后续规范 | 分别制定单位级统计、参照归一化、完整分析不确定性的所有者设计；逐项实施，不捆绑本轮 API | 上表判别案例通过；每个新结果有独立解释、方法准入、保留状态和完整测试 |

验收应先跑受影响的 `make test TESTS='...'`、窄范围 `make typecheck TYPECHECK_TARGETS='...'` 与 `make lint-agent LINT_TARGETS='...'`，共享契约变更再跑 `make check-agent`。公开面验收包括快照导出、Help 目标可达性、预算、独立冷进程 disclosure journey 和中英文示例；数值验收使用手算期望，不能由 Marivo 自己的输出生成期望。多次读取间插入源更新的测试只验证现行必要校验、拒绝条件与**无额外探测/固定版本/自动重试**，不得把一次通过命名为共同快照验收。运行时后端矩阵仅针对实际新增的执行路线选必要范围，本文不声称已做真实后端或发布验收。

## 完成判据

Agent 能从一个不可变逻辑定义继续要求合法依据，能区分“每地区渠道贡献”与“全国地区×渠道贡献”，能区分渠道分解与客单价计算组件分解；每个物化结果的目标、数值核对和限制可直接读取。不合法组合在最早可确定的时点给出具体修复。所有已接受切片有相应规范、实现、披露、数值/反例测试与必要 Runtime 证据；任何文档上的 proposed API 在完成这些条件前均不得描述为当前已支持。
