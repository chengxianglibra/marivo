# Marivo 分析代数

观察一个指标，就是为每个输出键计算一个值。**输出键决定报表粒度，进入归约的对象决定统计单位。** 一张按月出数的报表，既可以对订单金额求均值，也可以先算每位客户的消费总额，再对客户求均值。两者都是每月一个数，分母却不同。

本文给出 Marivo 分析模型的条件数学定律与当前优化器边界。带前提的数学等式不等于公开 API 或优化器支持清单；运行时契约分别由 [Population 与观察](populations-and-observation.md)、[AnalysisFrame](analysis-frame.md)、[算子与 Frame](operators-and-frames.md)、[时间语义](../temporal-semantics.md) 与 [语义表达式](../semantic/expressions.md) 拥有。

## 核心对象与求值

固定一次分析所用的数据实例 `I`。Metric 表达式 `m` 定义量的含义、来源分支、贡献时钟、归约次序、检查政策和单位。上下文 `Γ` 绑定本次筛选、所用总体、分组、报告期间和输出域规则。`Eval(I, m, Γ)` 仅在类型、绑定、覆盖、唯一性和方法前提满足时成功返回。一次观察中的所有指标共用输出域规则。以下四个对象描述一次完整的求值语义。

| 对象 | 含义 |
| --- | --- |
| 输出键集 `K` | 实际输出的有限个完整坐标元组，每个键占一行且**完整键唯一**。零轴标量的键集是 `{()}`，零行结果的键集是 `∅`。 |
| 贡献出现 `Cₖ` | 当前分支在键 `k` 下接纳的各次贡献。一次贡献可以是一笔订单、一个客户、一个带权样本、带唯一排序键的观测点，或一个已经完成的子结果。 |
| 结果列 `(V, x)` | `V ⊆ K` 是该列有有效值的键，`x: V → T` 给出这些值，`T` 是值类型。每列有自己的 `V`；Frame 另有行序、端点 presence 及冻结坐标等事实。 |
| 检查义务 `E` | 本次求值须满足的运行时断言集合（如 `nulls="error"`、数学定义域检查、输出域覆盖检查、排序唯一性检查）。各检查构成幂等交换幺半群 `(Bool, ∨, False)` 并携带确定性见证键；任一激活义务为真即整次求值失败。 |

把结果列记作 `q: K → T ∪ {NA}`，在 `V` 上取 `x(k)`，其余输出键取 `NA`。一个分支的基本归约式按输入形状分为三类：

$$
q(k)=\rho\bigl(\{\!\{\,v(k,c)\mid c\in C_k\,\}\!\}\bigr),\qquad k\in K
$$

$$
q_{\text{pair}}(k)=\rho_{\text{pair}}\bigl(\{\!\{\,(v(k,c),\,w(k,c))\mid c\in C_k\,\}\!\}\bigr),\qquad
q_{\text{ord}}(k)=\rho_{\text{ord}}\bigl(\{\,(t(c),\,v(k,c))\mid c\in C_k\,\}\bigr).
$$

双花括号表示有限多重集：两笔 10 元订单贡献两个 10。配对式覆盖加权均值与相关系数（按共同有效对过滤）；有序式覆盖 `first`/`last`，要求每个输出键下 `t(c)` 严格唯一，因此输入是按时间索引的有限偏函数而非无序多重集。把各组的出现标为 `(k, c)` 再取不交并，就得到总贡献集 `C` 与归属映射 `a: C → K`，各组正是 `a⁻¹(k)`。一笔事实可以通过不同的出现归入多个键。多来源指标的每个分支有自己的 `Cₖ`，各分支在共同输出键上完成后才组合。绑定身份、值、重数、检查义务与输出域描述的是问题的含义，执行器可选择符合这些条件的物理计划。[来源贡献与内部样本](populations-and-observation.md#contribution-occurrences-and-internal-samples)

数学等式在同一 `I` 上比较。实际的两次来源读取各有自己的读取状态，相同的 Metric 和报告期本身不能建立共同快照。来源绑定信任已声明的身份、基数和版本粒度，具体操作仍须完成其所要求的检查。[来源声明与检查](populations-and-observation.md#population-identity-and-set-semantics) · [Session 与求值归属](session-state-and-runtime.md#session-and-invocation-ownership)

## 输出键、支持域与贡献

<a id="8-可选身份域绑定"></a>

Population 表示带类型、按完整元组去重的有限键集。客户的复合主键是一个完整身份，收货城市与开票城市是不同角色。显式给出的客户键表示请求该身份，来源成员选择表示实际命中的身份。普通指标可以直接绑定订单等事实。显式使用同一个 Population `P` 时，它的位置决定其作用。

| 使用位置 | 对计算的约束 |
| --- | --- |
| `where=mv.in_population(P)` | 按完整键判断事实是否命中 `P`。同一事实有多个合格匹配也只保留一次。 |
| `by=P` | 输出 `P` 的全部键，包括没有事实的键。显式贡献条件生效后，`P` 必须覆盖来源所需的完整外层键（产生覆盖检查义务 `⋃ᵢ NecessaryKeys(mᵢ) ⊆ P`）。漏键在内部样本选择或结果裁剪前报错。 |
| `m.per(P)` 后接归约 | 为每个输出键选择与其共享轴相容的样本。`P` 可以选取子集，也可以包含没有贡献的样本。每个被选样本都按子指标的规则完成。 |

### 支持域代数 `Supp(·)`

不同分组模式下，输出键集 `K` 的构造规则不同，存在三处关键边界：

| 分组模式 | 输出键集 `K` | 附带约束 |
| --- | --- | --- |
| 显式 `by=P` | `K = P` | 附带全量覆盖检查 `⋃ᵢ NecessaryKeys(mᵢ) ⊆ P`；即使 `P = ∅`，必要键非空仍报错。 |
| 零轴 `by=()` | `K = {()}`（恒为单行） | 即使所有分支 `C_() = ∅` 仍保留单行；但显式空零轴 `by=mv.coordinates(by=(), keys=())` 输出 `∅`。 |
| 普通非时间分组 `by=(d₁,…)` | `K = ⋃ᵢ Supp(mᵢ)` | `Supp(mᵢ) = {k ∣ Cₖ(mᵢ) ≠ ∅}`；支持建立在空值处理和公式计算之前。 |
| 含报告日历轴 `by=(…, cal)` | `K = ⋃_{i ∈ B_timed} Supp(mᵢ)` | 仅带归期分支（含窗口分支与自带兼容时间轴的 `P.count()`）建立输出键；独立分支 `B_indep`（`time=None` 或无时间轴 `P.count()`）**不**把自身普通键并入 `K`，而是沿普通轴投影 `π_ord: K → K_ord` 拉回广播。 |

由于普通分组先取静态分支支持并集再做缺失补值和公式运算，同一次观察中的 `a + b` 自动在公共域上完成两侧：某侧没有贡献时，金额 `sum` 可以在另一侧建立的键上得到 `0`。一旦两张表已分别完成再做外对齐，缺侧则按端点 `presence` 一律取 `NA`。[观察域与覆盖](populations-and-observation.md#observation-domain-contributions-and-counts) · [来源公式的键并集与补值](../../../marivo/analysis/compiler/native_formula.py)

例如 A 仅在 1 日消费 40 元，B 仅在 3 日消费 100 元，`a`、`b` 分别是两人的金额 `sum`。在相同的普通按日设置下单独观察 `a`，结果只有 `(1日, 40)`；单独观察 `a + 0*b`，结果有 `(1日, 40)` 和 `(3日, 0)`。对结果行求均值分别得到 40 和 20。因此，在普通稀疏分组下消去 `0*b` 会一并删去 `b` 提供的键；只有在 `K` 已被外部固定（如 `by=P` 或 `by=()`）且检查与值语义均被保留时，才不改变键集。[稀疏公式与支持域测试](../../../tests/analysis/materialization/test_deferred_windows.py)

### 时间、窗口与单调编码

**日历粒度偏序与窗口。** 报告 `CalendarAxis` 给出有限候选期间目录。自然粒度构成偏序而非全序：`subday` 整除其父宽度，`day ≼ week/month/quarter/year`，`month ≼ quarter/year`，`quarter ≼ year`，而 `week ⋠ month/quarter/year`。受夏令时影响，同一民用标签或名义序号可对应不同实际 UTC 桶键，日历键不承诺成员构成连续 UTC 区间。有归期的分支用自己的贡献时钟解释范围和桶：普通桶使用名义半开区间与报告范围的交集；窗口在每个端点使用声明的贡献区间 `W(t)`，可以读到显示范围之前的贡献。相同客户连续两天出现，日去重人数各为 1，覆盖这两天的窗口去重人数仍为 1。[时间窗口](../temporal-semantics.md#metric-windows)

普通 `by=(calendar,)` 只输出实际支持的键，三个月的目录可以只有两行结果；要保留全部所选月份，使用显式输出 Population。如果一次带日历轴的观察仅含独立分支且没有其他兼容时间键来源，必须显式给出输出 Population。[日历目录与实际结果行](../../../tests/analysis/materialization/test_deferred_windows.py)

**窗口与 `per` 的非交换律。** 窗口与内部样本归约不可交换：
- `m.per(day).mean().window(W)` 先把贡献限定在端点窗口 `W(t)` 内，再在 `W(t)` 中发现实际日样本并求均值；
- `m.window(W).per(day).mean()` 先在报告范围与固定谓词下发现实际日样本，再在**每个日样本的端点**上独立求窗口 `W` 的值并求均值（历史读取不新增样本日）。[内部样本与窗口](../semantic/expressions.md#internal-samples-and-windows)

**单调编码边界定理与单射分桶延后。**
- **单调逆像边界律（选择下推）**：设物理时间表示到绝对瞬时的解码函数 `f: T_phys → T_inst` 在**整个被求值的物理域**上相对后端原生比较序单调不减（字符串编码须使用保字节/字典序的 collation，模糊民用时刻须按一致规则解析为统一偏早或统一偏晚），则半开瞬时区间 `[a, b)` 的原像必为物理域上的半开区间 `[⌈a⌉_f, ⌈b⌉_f)`（其中 `⌈x⌉_f` 是满足 `f(p) ≥ x` 的最小合法物理刻度；不存在时记为 `+∞`）。端点邻域无 DST 跳变（如 ±48 小时稳定检查）用于保证 `⌈a⌉_f`、`⌈b⌉_f` 本身可无歧义计算。这允许把范围过滤直接下推到未解析的 `DATE` 或有序编码列（如 `%Y%m%d`）。
- **单射键重索引律（分桶延后）**：设原始键到输出桶的映射 `g: T_raw → T_bucket` 在**实际出现的原始值集**（含 `NULL` 与非规范值）上**单射**（例如无 23/25 小时跳变的同区 `DATE` 或规范日编码），则 `(g ∘ a)⁻¹(g(t)) = a⁻¹(t)`，每组收到的多重集完全相同，故对**任意**归约 `ρ`（无需 L6），先按 `g(t)` 分组再归约均同构于先按原始键 `t` 分组归约、再对聚合结果的键施加 `g`。若实际数据违背规范编码声明而破坏单射，延迟映射会在聚合后产生重复输出键。[编码时间与分桶优化](../temporal-semantics.md#encoded-event-time-predicates) · [源端时间编译](../../../marivo/analysis/compiler/source_time.py)

### 多值关系

对事实 `f` 和标签键 `k`，存在性筛选问“是否有匹配”。按 `membership` 分桶则对每个不同的 `(f, k)` 产生一次贡献，重复路径命中的同一对只计一次。一笔 100 元订单属于两个标签，两个标签各得 100 元。若量度的是两次独立的关联事件，需保留事件身份；若要按权重分摊金额，还要给出权重及其业务条件。同一描述内的多个字段使用实际匹配的联合元组，只有独立的分桶描述才对各自的匹配集合取乘积。[分桶、重数与关联规则](populations-and-observation.md#contribution-occurrences-and-internal-samples)

在多值成员关系或重叠区间 Population 下，存在性筛选**不**对交集分配：`in_population(P ∩ Q)` 要求存在**同一个**同时属于 `P` 与 `Q` 的匹配键，而 `in_population(P) & in_population(Q)` 只要求分别命中 `P` 中某键与 `Q` 中某键；仅当映射单值（或每事实至多命中一个键）时二者才等价。

## 空值、检查义务与存在性语义

一次成功求值中，`k ∉ K` 表示没有结果行，`k ∈ K ∖ V` 表示有行而本列为 `NA`，`k ∈ V` 时读取 `x(k)`。数据库 `NULL` 和列中的普通缺值使用同一值级语义。数值 `0`、空字符串和**带有效位的浮点 `NaN`** 均属于 `k ∈ V`（有效值），各按其物理类型解释：例如对 `[NaN]`，有效样本数 `h = 1`，`sum([NaN]) = NaN`（不是 `NA`），且在比较中 `~(NaN < 0)` 为 `True`，而 `~(NA < 0)` 为 `NA`。绑定不合法、请求域漏键、读取失败或严格断言失败属于求值错误；已读范围内没有贡献，只说明本次问题的贡献集为空。[数值与有效性合同](operators-and-frames.md#null-empty-and-arithmetic-policies)

对一个已经存在的输出键，记 `n` 为真实贡献次数，`h` 为其中值有效的次数。显式名单带来的结果行可以有 `n = 0`。以订单金额为例，默认规则如下。

| 实际订单金额 | 订单数 `n` | 有金额的笔数 `h` | 金额 `sum` | 订单 `mean` |
| --- | ---: | ---: | ---: | ---: |
| 无订单 | 0 | 0 | 0 | NA |
| `[NA, NA]` | 2 | 0 | NA | NA |
| `[0]` | 1 | 1 | 0 | 0 |
| `[10, NA, 30]` | 3 | 2 | 40 | 20 |

`sum` 在 `n = 0` 时取有类型的零，在 `n > 0, h = 0` 时取 `NA`，其余情况累加有效值。`mean`、`min`、`max`、分位数在没有有效样本时取 `NA`。字段 `count` 和去重计数只计有效值；Entity 计数、Population 计数、Frame 的 `row_count` 分别计贡献出现、不同的完整成员键和当前结果行。[空贡献、全 NULL 与有效零](../../../tests/analysis/materialization/test_sum_null_contract.py)

### 检查义务与需求门控

要求每笔金额齐全时，可在支持的来源或内部归约上使用 `nulls="error"`；空贡献集通过这项断言（因为其中不含 `NA`）。普通算术与数学函数（如 `log`、`sqrt`）也会产生域检查义务。检查义务在表达式树内自顶向下按**需求掩码 `d: K → Bool`** 递归组合（根输出列初值 `d_root(k) = True`，嵌套子节点遵循 `d_child = d_parent ∧ local`），并在阶段边界上冻结：

- **普通操作数继承父掩码**：在 `a + b`、`0 * strict` 等普通算术中，每个操作数直接继承父节点的需求掩码 `d_child = d_parent`（在顶层即为 `True`），因此 `0 * strict` 仍保留对 `strict` 当前需求范围的检查，不能用零乘子消去。
- **条件分支按需收紧**：在 `cond.ifelse(a, b)` 中，对每个键 `k ∈ K`，`cond` 继承 `d_parent`，`a` 的掩码为 `d_parent(k) ∧ (cond(k) = True)`，`b` 的掩码为 `d_parent(k) ∧ (cond(k) ≠ True)`；在 `coalesce(a, b)` 中，`a` 继承 `d_parent`，`b` 的掩码为 `d_parent(k) ∧ (a(k) = NA)`。未选中的分支不触发其运行时检查，但仍参与静态类型检查与输出键支持域 `K`。[来源归约实现](../../../marivo/semantic/_numeric_lowering.py) · [按需分支检查实现](../../../marivo/semantic/_numeric_recipe.py) · [分支需求测试](../../../tests/formal/verify_query_rewrites.py)
- **阶段边界不可逆**：一旦某个 `observe` 或 Frame 算子把表达式捕获为已完成输出，下游 `select` 裁列、`where` 筛选或 `top(0)` 均不能消除上游已经确立的检查义务。[检查范围的执行测试](../../../tests/formal/verify_query_rewrites.py)

普通数值算术传播所需操作数的 `NA`。`/` 固定将零分母映为 `NA`（包括 `0/0`），并将数值源操作数在除法前转为 `float64`。加权均值的分子、分母使用同一批值与权重都有效的样本，支持有符号权重，有效权重和为零时取 `NA`。相关系数也使用共同有效的配对，样本不足（`< 2`）或一端为常量时取 `NA`。[除法实现](../../../marivo/semantic/numeric_operations.py) · [算子与样本合同](operators-and-frames.md#null-empty-and-arithmetic-policies)

可空布尔谓词按 Kleene 三值逻辑组合，`where` 只保留 `True`。允许空值的普通分类键把 `NULL` 当作可匹配的类别，比较完整键时两个对应的 `NULL` 分量视为相等；Entity 身份则要求每个主键分量都非空。[Population 的键与集合语义](populations-and-observation.md#population-identity-and-set-semantics)

有效键集 `V` 是数学记法，列可直接复用原生有效性位图。`sum` 在可空累加值之外，只需记住是否见过真实贡献，缺失处理的额外状态每组为常量；现有来源实现用同一聚合中的行数与有效值数作判断。分析需要披露这两个数时，可把它们作为指标输出。[来源聚合的空组判断](../../../marivo/semantic/_numeric_lowering.py)

### 外对齐的 `presence` 代数

外对齐后，从输出键 `j` 到每个端点 `s` 各有一个部分映射，并附带独立的不可空布尔列 `pres_s: K → {True, False}`：
- **存在不变量**：对属于端点 `s` 的任意值列 `c`，恒有 `V_c ⊆ {j ∈ K ∣ pres_s(j) = True}`。当 `pres_s(j) = True` 时取该侧原值（它本身仍可为 `NA`）；当 `pres_s(j) = False` 时，该侧所有值列必为 `NA`，即使原列是 `sum` 或 `count`。名单上的客户没有订单，仍可有一行 `pres_s = True`、金额 `sum = 0` 的结果。
- **结构传播与擦除**：`where`、`top`、`select`、`fix` 同步保留所有行级 `presence`；嵌套 `align`/`compare` 为历史 `presence` 加前缀并在缺外层行时置 `False`；`reduce`（即便保留全部轴）擦除全部行级 `presence`。[Frame 的值与 presence](analysis-frame.md#名称与-presence)

## 核心运算、对应与分解结构

### 三种基本动作

| 动作 | 定义 | 典型用途 |
| --- | --- | --- |
| 按键取值（拉回） | 对 `r: J → K`，新列在 `j` 上取 `q(r(j))`，新有效域为 `r⁻¹(V)`；若 `r` 为部分映射则需明确缺键按来源空集 `ρ(∅)` 还是按外对齐 `NA` 完成。 | 切片、沿已声明的对应取数、把组统计映回原行 |
| 逐键运算 | 在共同 `K` 上按算子规则计算 `g(q₁(k), …, qₙ(k))`，并按需求掩码传播检查义务。类型、单位、缺值和算子定义域在这一层生效。 | 来源分支完成后求比率、对应后的两期求差 |
| 按键归约（前推） | 选择目标键及其贡献出现 `Cₖ`，对每个键按基本式应用 `ρ`。 | 对事实聚合、对内部样本求均值、对当前 Frame 行作统计 |

**嵌套归约。** 设 `Jₖ` 是键 `k` 下的样本键集，内层域是所有实际的 `(k, j)`，其中 `j ∈ Jₖ`。`m.per(P).mean()` 先对每个 `(k, j)` 完成整个 `m`，再在 `k` 内对这些值求均值。同一客户可以在多个地区分别成为样本。字段推导的样本来自实际联合键，显式样本域还可以包含零贡献客户。[样本归约与完成顺序](populations-and-observation.md#contribution-occurrences-and-internal-samples)

同一个月里，A 有两笔 10、30 元的订单，B 有一笔 100 元订单，名单中还有未下单的 C。下面四个问题有相同的输出键。

| 问题 | 进入最外层均值的样本 | 结果 |
| --- | --- | ---: |
| 每笔订单平均多少钱 | 三笔订单的金额 10、30、100 | `140/3` |
| 每位购买客户平均消费多少 | A、B 的消费总额 40、100 | 70 |
| 购买客户各自的订单均价，等权平均是多少 | A、B 的订单均价 20、100 | 60 |
| 名单内每位客户平均消费多少 | A、B、C 的消费总额 40、100、0 | `140/3` |

若给 A 加一笔 20 元订单，订单均价变为 40，名单内客户的平均消费变为 `160/3`。若原数据唯一的变化是给 C 加一笔金额为 `NA` 的订单，C 的小计从 `0` 变为 `NA`。名单仍有三人，默认均值的有效分母变为 2，结果为 70。

<a id="53-参照与共同有效样本"></a>

**当前行与参照。** `Frame.reduce` 以当前行作为贡献；零轴 `reduce(by=())` 在空输入上输出单行 `K = {()}`。若 `h: K → J` 是完整分组键的投影，`Frame.over` 先在每组统计，再沿 `h` 把组值映回原行；空输入 `K = ∅` 映回后仍为空（即使 `by=()`）。`Column.share` 用当前值除以映回的分组 `sum`。先为 `(1, 3)` 算份额再保留第一行得 `1/4`，先保留第一行再算得 `1`。业务构成比要求分子与参照使用同一贡献口径：若各分子是同一可加量在完整不交分区上的小计，参照是非零总额，且各份额均有定义，则在精确算术下它们之和为 `1`。[分区统计、广播与份额](operators-and-frames.md#partition-statistics-and-current-value-shares)

**对齐与比较。** 两个或多个已完成结果的轴要建立完整双射对应。普通轴可显式给出 `on`，也可接受唯一同名、相容完整键的默认约定；调用方声明业务对应，系统验证 Session、轴角色、完整键结构和时间解释。结构相容且源自不同 Entity 的普通轴也可以显式对应。数学上的精确对齐还要求对应后的实际联合键域相等。公开 `align` 和 `compare` 固定使用 outer，缺侧给出 `NA` 与独立 `presence`：
- `align` 可保留双端时间并集；带显式时间 `match` 时生成单层配对轴 `(t_L, t_R)`，每侧键可缺省但不同时缺省，且不允许对已有配对轴二次对齐。
- `compare` 支持单个裸基期或有序命名多基期，保留本期时间轴并为本期及每个基期维护独立 `present` 列。时间对应必须是本期与基期声明目录间的**部分双射（单射对应）**，从而保证映射后的完整行键唯一；各实际基期时间行须能映入本期声明域（无法映入或发生目标碰撞则报错），映入后仅基期有行的联合键也保留。相对变化取 `(本期 − 基期) / abs(基期)`。[Frame 对应与比较](analysis-frame.md#correspondence-and-comparison) · [基期时间检查](../../../marivo/analysis/methods/frame_comparison.py)

**行序与 `Top` 算子。** `AnalysisFrame` 维护可观察的行序。`Frame.top(n, by=score)` 先按评分 `score` 排序，再以完整键 `(k₁ ASC NULLS FIRST, …, kₘ ASC NULLS FIRST)` 确定性打破平局。由于浮点的 IEEE 754 比较在 `NaN` 上不构成全序，必须显式约定 `NaN` 位置：在本地 Arrow/pandas 内核中，无论 `ASC` 还是 `DESC`，有序浮点（含 `±∞`）排在前面，有效 `NaN` 紧随其后，缺失 `NA` 排在最后（即 `有序浮点 < NaN < NA`，同级平局再由完整唯一键打破）；若向源端下推浮点 `Top`，须显式对齐后端 `NaN` 序（如 DuckDB 将 `NaN` 视为大于 `+∞`，`DESC` 默认排在最前）。在确定 `NaN`/`NA` 位置且完整行键唯一后，该排序是**严格全序**；保留全输入检查义务时满足：
- 复合律：对相同评分与方向，`top(n₁) ∘ top(n₂) = top(min(n₁, n₂))`；
- 分区合并律：当 `A` 与 `B` 按完整键不交（或同键行完全一致）时，`Topₙ(A ∪ B) = Topₙ(Topₙ(A) ∪ Topₙ(B))`（输入检查义务仍须在完整 `A ∪ B` 上汇总）；
- 非交换边界：`top` 一般不与 `where`、`over`、`share` 或 `fix` 交换。[本地 Top 排序实现](../../../marivo/analysis/methods/query_values.py)

**归因分解（`Frame.attribute`）。** 归因在固定父键上解释已准入目标的水平值或双端变化量，满足三类闭合恒等式：
- **可加分解**：当细组构成父贡献的完整不交分区（无重叠多值分配或未匹配丢弃）且满足闭合前提时，可加目标的细组贡献之和闭合到新鲜父总额 `∑_g Δm_g = Δm`。
- **对称 Rate/Mix（Bennet）分解**：对满足父分母为正、各组与各对权重非负且零支持组分子为零的比率目标 $R = \sum_g w_g r_g$（$w_g = D_g / D$），称满足 $D_g > 0$ 为该端有支持；双端均满足 $D_{g,0} > 0 \land D_{g,1} > 0$ 的细组定义
  $$\text{within}_g = \tfrac{w_{g,0}+w_{g,1}}{2}(r_{g,1}-r_{g,0}),\qquad \text{mix}_g = \tfrac{r_{g,0}+r_{g,1}}{2}(w_{g,1}-w_{g,0}),$$
  恒满足 $\text{within}_g + \text{mix}_g = w_{g,1}r_{g,1} - w_{g,0}r_{g,0}$；仅一端满足 $D_g > 0$ 的进出组全部归入 `entry_exit`，全部细组效应之和精确闭合到 $R_1 - R_0$（相对归因同除以非零 `|R_0|`）。[Rate/Mix 效应实现](../../../marivo/analysis/methods/attribute_contribution_effects.py)
- **Shapley 置换博弈**：对非线性成对目标，以联合细键为参与者集合 $G$；对任意联盟 $S \subseteq G$，特征函数 $v(S)$ 是将 $S$ 中所有组取本期、其余组 $G \setminus S$ 取基期后的整式重算，单个参与者沿置换加入的边际贡献为 $v(S \cup \{g\}) - v(S)$，每条采样置换路径满足效率公理 $\sum_{g \in G} \phi_g = v(G) - v(\varnothing)$。满足 L6 摘要可重构条件的目标在本地合并各参与者状态求 $v(S)$，不满足的目标回退到源端原生联盟查询。[归因与博弈契约](populations-and-observation.md#captured-source-contribution-targets) · [精确归因实现](../../../marivo/analysis/methods/frame_attribute_contribution.py) · [Shapley 博弈实现](../../../marivo/analysis/methods/frame_attribute_shapley.py)

**量纲代数与可加性权限半格。**
- **量纲**：可分解单位构成原子符号上的自由阿贝尔群 $\mathbb{Z}^{(\Sigma)}$（乘法为指数向量相加，除法为相减，约分消去公因子，无量纲 `"1"` 为单位元），并附加不透明单位与吸收元 `None`（未知单位）；加减法要求兼容相等的单位，`ms.rescale` 作为显式声明的态射更改目标单位。[单位代数实现](../../../marivo/semantic/unit_algebra.py) · [单位代数形式化验证](../../../tests/formal/verify_unit_algebra.py)
- **可加性权限**：`additive_all()`（全集）、`additive_except(E)`（补集）、`additive_over(A)`（显式轴集）与 `non_additive()`（空集）构成交半格。同单位线性行算术取权限交集，常数缩放保持权限，乘除法重置为空集除非显式再声明。[可加性权限声明](../semantic/expressions.md#sum-permission-declarations)

**重新绑定。** 对 Metric 改换期间、条件或样本，是在新上下文中重新求值。固定 Frame 的选择和归约使用捕获的键与值。`session.population_from(frame, on=...)` 投影并去重所选普通轴的完整联合键，按显式 Field 或 Entity 目标建立新的语义绑定。版本化目标字段省略锚点时，须能确认沿用同一个未锚定的直接字段；换绑到另一个版本化字段须给出目标时间锚点。[固定键的来源桥接](populations-and-observation.md#fixed-data-bridge-to-a-new-source-question) · [版本化字段的准入检查](../../../marivo/analysis/dsl/frame_population.py)

取得更细分类或新事实需要新的显式来源输入。独立恢复的 Frame 使用已存数据，`source_reads` 供审计。持有合格来源声明的进程内 Frame 可用 `attribute` 固定当前父键并发起新读，此时旧观察值与本次来源总额各有自己的读取状态。[Frame 载体](analysis-frame.md#数据结构与载体)

## 候选键与函数依赖传播（`UniqueKeys`）

完整键唯一是 `Population`、`AnalysisFrame`、有序 `first`/`last` 与外对齐的结构不变量。对关系算子维护候选键集合 $\mathcal{U}(R)$（每个元素是能唯一确定行的列集）：

| 算子 | 候选键传播规则 |
| --- | --- |
| `Aggregate(groups=G)` | 建立候选键 `G`（全局分组 `G = ()` 建立空键 `()`，即至多一行）。 |
| `Distinct` | 建立全部输出列为候选键，并保留上游在输出中完整保留的候选键。 |
| `Project` / `Filter` / `Sort` / `DropNull` | 仅保留其全部列被原样直传（未经非单射变换）的候选键。 |
| `Limit(n)` | 保留上游候选键；当常量整数 `0 ≤ n ≤ 1` 时额外建立空键 `()`。负数可表示从尾部选取多行。 |
| `Intersection` / `Difference` | 保留左输入候选键；`distinct=True` 时额外建立全输出列键。`Union` 丢弃跨分支唯一性（仅 `distinct=True` 建立全输出列键）。 |
| `Join(L, R)` | `semi`/`anti` 保留 `L` 的候选键；`inner`/`left` 当连接等值列覆盖 `R` 的某候选键时保留 `L` 的候选键；`inner` 对称保留 `R`；`cross` 产生 `L × R` 候选键对。**限制**：字符串等值因可能受非二进制 collation 影响（如大小写折叠）而不作为唯一键等值依据。 |

关系推导和常规贡献读取只用非浮点列的 SQL 分组或去重建立新候选键。后端可能分别保留正零、负零或不同编码的有效 NaN，而完成阶段的键校验将相应值视为同一键。已有的精确候选键仍可按上表传播。

这些调用内局部事实只消除冗余的去重/重复键扫描（如 `first`/`last` 排序唯一性检查与 `Top` 重复键聚合），不替代 `NULL`、覆盖或数值检查。[候选键推导实现](../../../marivo/analysis/compiler/unique_keys.py)

## 等式在什么条件下成立

两个程序可以互换，首先要求**可成功求值的输入域与检查义务等价**。先固定业务问题的贡献身份与重数、有效样本、来源时钟与版本、单位和参照。两边的完整输出键应当一一对应并保留绑定，每个键的行存在性、值、有效性及 `NaN`/`NA` 区分须相同。方法可观察的行序与必要失败也是结果的一部分。

下述摘要等式采用精确算术。在实际后端改写求和顺序、浮点均值或 Decimal 运算，还要满足原生精度、舍入、溢出和完成边界。近似计算另须遵守所选算法及参数的约定。[物理数值合同](operators-and-frames.md#physical-numeric-and-temporal-carriers)

在固定键和共同有效的实数样本上，精确求和对加法具有线性。若两列为 `x = (1, NA)`、`y = (NA, 2)`，逐行相加得到 `(NA, NA)`，因此 `sum(x + y)` 为 `NA`，而 `sum(x) + sum(y)` 为 `3`：两条路径使用了不同的有效样本。

### L6 摘要分区与上卷

设摘要状态集为 `S`，合并运算 `⊕` 满足结合律和交换律，空状态 `e` 是其单位元。`η` 把单次输入编码成状态，`φ` 从状态算出最终值。若对每个允许的输入集合 `B`（无序多重集、配对多重集或唯一时间索引对集）都满足下式，就可以先分别归约再合并摘要。

$$
s(B)=\bigoplus_{u\in B}\eta(u),\qquad s(\varnothing)=e,\qquad \rho(B)=\varphi(s(B)).
$$

要求分解前后有相同的定义域、检查义务和结果。若 `B` 按**贡献出现**不交分为各个 `Bᵢ`，则 `s(B) = ⊕ᵢ s(Bᵢ)`。用 `a: C → K`、`h: K → J` 表示两次归属，沿 `h ∘ a` 直接合并和分两次合并会收到同一批出现，空组贡献单位元。原程序若要求完成并检查细组，改写也须保留这些检查。粗化若改变了成员选择、窗口或内部样本，必须按新问题重新确定贡献。

| 目标 | 足够的状态（精确算术） | 合并后如何完成 |
| --- | --- | --- |
| 计数 `count` / `entity.count` / `row_count` | 非负整数计数 `c`（单位元 `0`） | 分组计数相加 |
| 极值 `min` / `max` | 组内有效极值，空/全 `NA` 用 `NA` 作单位元 | 对非 `NA` 取更小/更大值（精确全序类型；浮点 `NaN` 须遵循所选后端的原生序与合同） |
| 默认金额 `sum`（`nulls="ignore"`） | 三态：`E`（空）、`U`（全 `NA`）、`V(s)`（含有效值且和为 `s`） | 有有效组时累加各 `V(s)`；否则有 `U` 取 `NA`，全为 `E` 取类型化 `0` |
| 严格金额 `sum`（`nulls="error"`） | 两态 + 失败吸收元：`V(s)`（单位元 `V(0)`）与 `⊥_err` | 含 `⊥_err` 报错，否则累加 `s`（空组与有效零同为 `V(0)`） |
| 有效值均值 `mean` | 分组有效值之和与有效数 `(sum, h)` | 分量分别相加，再除以总有效数，零个有效值取 `NA` |
| 加权均值 `weighted_mean` | 共有效对的加权分子与权重和 `(∑_{co-valid} v·w, ∑_{co-valid} w)` | 分量相加再相除，有效权重和为零取 `NA` |
| 有序 `first` / `last` | **仅当 `(父键, t)` 唯一性已证明**（或合并互不相交的时间桶且桶内已检）时：极值时间-值对 `(t_ext, v_ext)`（`include_null=False` 先滤去 `NA`）；否则须保留完整时间集合以执行 `include_null` 过滤前的唯一性检查 | 取更早/更晚的 `t_ext` 及其对应值；未证明唯一性时须在合并粒度独立检查跨组时间重复 |
| 精确去重人数 `count_distinct` | 各组有效用户身份集合 | 先取并集，再数不同身份 |
| 精确分位数 `quantile` / `median` | 各组的有效值多重集 | 按重数合并，再按同一插值方法取分位数 |
| Pearson 相关系数 `corr` | 共有效对的 6 元充分统计量 `(n_pair, ∑x, ∑y, ∑x², ∑y², ∑xy)`（或平行中心矩） | 分量合并后算协方差与方差，样本 `< 2` 或常量端取 `NA` |
| Spearman 相关系数 | 各组的共有效原始值对多重集 | 必须合并原始对后重新求全局秩再算 Pearson（不可定长压缩） |
| 近似 Sketch（`approx_nunique` / `approx_quantile`） | 标量估算值本身不可并组；同后端同参数的 HLL 内部寄存器虽构成精确交换幂等幺半群，但各后端算法与参数不兼容且不跨边界传输原始 Sketch 状态，而 TDigest 合并还依赖顺序（非结合/非交换） | 禁止跨后端合并或对分组估算直接求和，须在目标域上原生执行 |

业务上还须允许沿所移除的轴合并该量。两天的期末库存都是 10 件，当前两行求和得 20 件，第二天的期末库存仍为 10 件。重叠标签也会重复订单。若同一客户横跨两个地区，求整体客户均值需先用原贡献或充分摘要算出每位客户跨地区的值，再对不同客户等权求均值。[当前行与原始指标](operators-and-frames.md#current-cells-original-metrics-and-windows)

### T1 后续计算的信息充分性

在约定的输入空间上，`R(X)` 是可复用的信息，`T(X)` 是下一步想求的答案（含必要失败结果 `⊥_err`）。存在良定义的 `H` 使 `T = H ∘ R`，当且仅当

$$
\forall X,Y,\quad R(X)=R(Y)\ \Longrightarrow\ T(X)=T(Y).
$$

正向来自函数性。反向对每个保留值选一个产生它的输入，将该输入的答案作为 `H` 的值，上式保证选择哪个输入都相同。它判断信息充分性，算法成本要另外分析。

固定输出名单 A、B。第一份数据只有 B 的一笔 `NA` 订单，第二份另加 A 的一笔 0 元订单。两份数据的分组 `sum` 都显示 `(0, NA)`，对这两行再求和都得到 `0`；可原始订单 `sum` 分别为 `NA` 和 `0`。按 T1，只保留分组键与完成值，无法回答这两份数据的原始总额。同理，地区总额无法还原客户名单，日去重人数无法还原周去重人数，分组相关系数与配对数也无法还原合并相关系数。[默认 sum 与当前行统计](operators-and-frames.md#current-cells-original-metrics-and-windows)

### T2 仅用最终值并组的判据

对带错误吸收元扩张后的归约 `ρ: M → T ∪ {NA, ⊥_err}`，只用各组最终值就能处理**任意并组（含空组）**，当且仅当在 `ρ` 的像上存在结合、交换的运算 `⊗`，满足 `ρ(A ⊎ B) = ρ(A) ⊗ ρ(B)`，单位元为 `ρ(∅)`。这里的相等按可观察结果判断：两个 `NA` 相等，两个同类有效 `NaN` 按其载体观察规则判断，错误按失败结果判断。若能只凭两个值并组，该运算就由上式唯一定义，多重集并的结合、交换和单位律随之成立。

更一般地，若对每个有限后续输入 `D` 都有 `ρ(A ⊎ D) = ρ(B ⊎ D)`（Myhill-Nerode 同余），则 `A` 与 `B` 可用同一摘要状态表示；任何充分的可合并摘要都须区分会被某个 `D` 区分的输入。
- 对精确实数、默认忽略 `NA` 的 `sum`：空组 `∅` 与有效零 `[0]` 当前都显示 `0`，但追加 `D = {{NA}}` 后前者得 `NA`、后者得 `0`，故三态 `{E, U, V(s)}` 是**最小**充分摘要。
- 对精确实数、严格 `sum(nulls="error")`：对任意 `D`，`∅ ⊎ D` 与 `[0] ⊎ D` 要么同时因 `D` 含 `NA` 而失败，要么同时得 `sum(D)`，因此空组与有效零同余，只需 `{V(s), ⊥_err}` 即可直接用最终值并组。

### 选择、映射、限制与 Population 集合律

其余定律均作用于已绑定的域，并显式标出值等式与检查义务的前提。

| 定律 | 条件与结论 |
| --- | --- |
| L1 固定选择（短路 vs 全定义） | （a）**顺序短路融合**：对同一输入上任意逐行谓词 `p, q`（不含跨行 `over`/`share`），连续筛选等价于从左到右短路合取 `σ_q(σ_p(R)) = σ_{p ∧→ q}(R)`（一般**不可交换**，因 `q` 可能在 `¬p` 行上报域错误）。<br>（b）**Kleene 合取与交换**：**若** `p, q` 在整个输入域上**全定义（无求值失败）**，则 `σ_q(σ_p(R)) = σ_{p ∧ q}(R) = σ_p(σ_q(R))`（全定义是充分而非必要条件）。`NA` 取反仍为 `NA`，而有效浮点 `NaN` 比较取反可得 `True`，故取反筛选一般不等于集合补集。 |
| L2 投影、关系像与逆像 | 对任意关系 `R ⊆ X × Y`：关系像 `R(S)` 保持并集，对任意两集合保持交集需要 `R` **单射（共函数，每目标至多被一个源命中）**。存在性逆像 `R⁻¹(T)` 保持并集，对交、差的普遍保持需要 `R` **偏函数（每输入至多命中一个目标）**，保持相对于全部输入的补集还需要 `R` 处处左全（全函数）。重叠时间区间也可能产生多个匹配；单调编码边界下推属逆像/选择律。 |
| L3 同源成员约束 | 对选中集合 `H` 与任意关系 `R`（不要求单值），在相同读取状态与一致版本锚点下，`H ∩ R⁻¹(R(H)) = H` 当且仅当 `R` 在 `H` 上**左全**（`H ⊆ dom(R)`，即每个 `h ∈ H` 至少有一条完整非空外键/有效路径见证）。悬空外键、空外键、中间表缺行或目标版本过滤都会破坏左全性；`R⁻¹(R(H))` 本身还可能包含其他共享目标的源成员，消去成员过滤时必须保留原 `H`。 |
| L4 映射合成 | 连续沿 `r: J → K` 和 `s: L → J` 取值，在 `l` 上都得到 `q(r(s(l)))`。在来源侧先变换上下文再求 Metric 同样按函数次序合成，须保留每步绑定、版本与检查域。 |
| L5 逐键合成 | 在同一键域上先用 `f` 后用 `g` 等价于逐键用 `g ∘ f`，前提是两步的值政策、必要检查以及条件分支的**需求掩码**（`ifelse`/`coalesce`）全部得以保留。`m / m` 在零处为 `NA`，不能无条件约为 `1`。 |
| L7 归约后取值（拉回） | 对 `a: C → K` 与 `r: J → K`，键 `j` 对应的出现恰是所有满足 `a(c) = r(j)` 的 `(j, c)`，因此在 `r(j)` 处读取已合并摘要的**值分量**等于在这批出现上合并同样的摘要（`r` 非单射时出现会复制，之后再对 `J` 归约不会还原原多重集）。整体求值 `Eval` 等式另须满足：（1）若 `r` 非满射（限制到真子集），须保留原 `K ∖ r(J)` 上的全部必要检查义务；（2）若允许 `r(j) ∉ K`，须区分来源空集完成 `ρ(∅)` 与 Frame 外对齐缺侧 `present=False` / `NA`。 |

<a id="84-保留原坐标的分类是图扩展"></a>

**`Population` 图扩展、乘积与路径律。**
- **`classify` 图扩展**：单值分类可写作 `k ↦ (k, g(k))`（若已存在兼容轴则复用为恒等映射）。在固定的完整键、同一读取状态和合法时间绑定下，此映射保留原键故单射，对**全定义标量字段 `g`** 保持行数及并、交、差。对**新增 identity 角色**分类，目标身份缺失会直接报错而非填 `NULL`，因此差集/交集分配律（如 `(P ∖ Q).classify(g)` 与 `P.classify(g) ∖ Q.classify(g)`）额外要求 `g` 在 `P ∪ Q` 上全定义无错。随后仅投影分类值则可能合并成员。
- **乘积与零轴单位元**：`P × ∅ = ∅`，`P × {()} ≅ P`，`(P₁ ∪ P₂) × Q = (P₁ × Q) ∪ (P₂ × Q)`；当 `p` 仅引用 `P` 的轴且全定义（或已知 `Q ≠ ∅`）时，`σ_p(P × Q) = σ_p(P) × Q`。
- **乘积成员因式分解**：对不相交轴域的乘积 `P₁ × P₂`，令事实 `f` 的联合匹配集为 `M(f) ⊆ X₁ × X₂`。**当且仅当各轴组的匹配相互独立**（即 `M(f) = π₁(M(f)) × π₂(M(f))`，例如各轴均为单值、时间轴按事实自身时钟匹配、或各轴来自互不共享扇出前缀的独立路径）时，成立
  $$\text{in\_population}(P_1 \times P_2) \iff \text{in\_population}(P_1) \land \text{in\_population}(P_2).$$
  若两轴共享多值扇出（如订单明细的 `(category, brand)` 联合匹配 `(A, X)` 与 `(B, Y)`，而 `P₁ = {A}`、`P₂ = {Y}`），则左侧为假、右侧为真，不可拆解。[联合成员谓词实现](../../../marivo/analysis/materialization/population_condition.py)
- **路径复合**：沿多跳路径 `p₁ · p₂` 组合关系时，仅当各中间节点共享同一版本绑定、目标存在性检查规则一致且在同一读取状态下，才有 `image(p₁ · p₂) = image(p₂) ∘ image(p₁)` 与 `preimage(p₁ · p₂) = preimage(p₁) ∘ preimage(p₂)`。
- **固定坐标**：`Frame.fix` 先按 NULL-safe 等值固定一个普通轴的值，再投影其余轴；该投影在选中子集上单射，故保留值、单位、`NA`、`presence` 和相对行序。固定最后一轴后的零行结果仍为 `∅`。[Population 集合语义](populations-and-observation.md#population-identity-and-set-semantics) · [固定坐标](operators-and-frames.md#single-member-coordinate-restriction)

### 实现编号与理论定律的对应

#### 当前编译器已实现的优化通道

| 数学依据 | 当前编译器行为 | 代码入口 |
| --- | --- | --- |
| L1 固定选择（短路与全定义） | 合格公开 Frame 调度合并相邻的多步 `where` / `fix`，按原顺序仅对存活行求值，最后物化一次。每个中间结果只允许一个消费者且无需发布或检查点。运行时布局变化按原步骤回退。内部 `ResultProgram` 只对同输入域的全定义标量比较、布尔取反和 NULL 判定合并 Kleene 掩码，随实际载体重新验证资格。 | [Frame 计划](../../../marivo/analysis/compiler/frame_filter_fusion.py) · [局部表达式资格](../../../marivo/analysis/model/local_laws.py) · [序列证明](../../../tests/formal/verify_selection_sequences.py) |
| L5 相邻投影（`L5_select_fusion`） | 同一键域、单消费者且无独立检查点的逐行 `SelectValues` 链共享最终 Frame 物化。各逻辑阶段的载体、求值顺序和必要检查继续有效，下游丢弃的可能失败列仍求值。运行时输入布局变化重放原投影。 | [投影计划](../../../marivo/analysis/compiler/frame_select_fusion.py) · [形式化检查](../../../tests/formal/verify_select_projection_composition.py) |
| 检查保留下的后向需求裁剪 | `required_slots` 在单次逆拓扑遍历中裁剪下游不再传输的普通投影列、未消费的 `ComponentRead` 组件和 `LiteralValues` 列，同时保留全部 `numeric_check` / `predicate_check` / 存在标记与上游必做检查。 | [后向需求分析](../../../marivo/analysis/compiler/optimization.py) |
| 同域共享聚合输入（`source_block`） | 同次来源观察中，来源、关系路径、版本、贡献时钟、谓词、成员、键、NULL 政策和载体均兼容的普通聚合共享同一分组 SQL 块；每个归约仍保留各自值与检查出现，物理扫描次数由后端决定。 | [公共聚合合并](../../../marivo/analysis/compiler/optimization.py) · [普通来源聚合](populations-and-observation.md#observation-domain-contributions-and-counts) |
| 纯归约确定性与检查幂等性（`common_read_cse`） | 同一读范围内，归约方式、操作数、权重、NULL 策略、类型与单位均匹配的确定性归约和检查可以共享物理计算。每个逻辑别名、分支的求值需求与检查义务分别保留。近似分位数继续独立调用已选内核。 | [公共聚合降级](../../../marivo/analysis/compiler/observation.py) · [内部样本投影](../../../marivo/analysis/materialization/sample_fusion.py) · [独立检查回归](../../../tests/analysis/materialization/test_query_optimization.py) |
| 嵌套内部样本源端融合 | 合格同源数值内部样本下推为嵌套原生关系，每层先在完整外/内键粒度上完成子指标再施加外层归约；兼容的多重精确分位数共享分布状态。 | [内部样本融合](../../../marivo/analysis/materialization/sample_fusion.py) · [样本降级](../../../marivo/analysis/compiler/sample_reductions.py) |
| 源端键内联、覆盖下推与终端融合 | `fuse_query_keys` 将同源 `in_population` 成员键、`P.count()`、Population 检查与输出域覆盖 `anti-join` 折叠到源端；`fuse_query_terminal` 将末端合格 `Filter`/`Select`/`Top`/`PopulationValues` 与全范围 `_check_summary` 融合为单次来源查询，并保证失败见证给出同一实际行的完整键。 | [键与终端融合](../../../marivo/analysis/materialization/query_key_fusion.py) · [终端融合](../../../marivo/analysis/materialization/query_terminal_fusion.py) · [失败证据组合](../../../marivo/analysis/materialization/native_query_checks.py) |
| Top 源端有界下推 | 对单消费者的直接待执行 `ReadQuery`（普通 `member` 轴 + 直接 `int64` 评分列），在源端同时计算全输入数值/NULL/重复键汇总与 `LIMIT n`，再由本地 Top 恢复展示顺序。 | [Top 下推](../../../marivo/analysis/compiler/frame_top.py) |
| 候选键传播（`UniqueKeys`） | 沿关系算子传播精确候选键，消除冗余的 `DISTINCT`、`first`/`last` 重复序扫描与 `Top` 重复键聚合。 | [候选键传播](../../../marivo/analysis/compiler/unique_keys.py) |
| 单调逆像边界下推与单射分桶延后 | 合格端点上将时间范围直接下推为原始 `DATE` 或有序编码列的半开常量比较；同区 `day` 日历在无 DST 异常区间内先按原日值分组，再于分组后恢复 UTC 桶键。 | [源端时间优化](../../../marivo/analysis/compiler/source_time.py) |
| L6 摘要状态（仅归因博弈） | 公开相邻 `Frame.reduce` 各自完成，不做自动跨步归并；Shapley 归因对合格目标复用 L6 充分状态以本地合并联盟值，其余回退原生联盟查询。 | [Frame 归约](../../../marivo/analysis/methods/query_values.py) · [Shapley 状态](../../../marivo/analysis/methods/frame_attribute_shapley.py) |
| 帧内同求值公共子表达式缓存 | 单次 `evaluate_frame` 内复用相同的分组索引、`over` 分区结果与配对统计端点求值（按带类型字面量区分缓存键）。 | [帧内求值缓存](../../../marivo/analysis/methods/frame_evaluation.py) |

#### 理论可推导、当前编译器尚未通用实现的条件改写

以下改写可由本文定律严格推导，**当前编译器尚未通用落地**，仅作为后续优化设计的代数规范与健全性前提：

| 改写方向 | 代数依据 | 必须满足的健全性前提 |
| --- | --- | --- |
| 同源多分支条件聚合合并（`FILTER WHERE` / `*If`） | 基本式 + 支持域并集 + 三态 `sum` | 同源、同路径/版本、同归期时钟与同分组键；按 $\bigvee_i p_i$ 扫描并分别统计每分支 $(n_i, h_i)$、`sum_i` 与 `ANY(p_i AND v_i IS NULL)`，确保空组 `0`/全 `NA`、键并集与每分支检查域完全不变。 |
| L6 窗口预归约与 SQL 窗口函数下推 | L6 + 窗口切片分解 | 归约满足精确 L6 同态（`sum` 三态、`count`、`min`/`max`、精确 `mean`/`weighted_mean`，以及桶内唯一性已检且跨桶时间不交的 `first`/`last`）；窗口边界与输出桶对齐（`all_history`/`since`/`to_date`/`trailing_periods`）；预归约须涵盖显示范围之前的必要历史桶，并对每个受支持普通键先与日历目录左连接补全无当前桶贡献的端点后再执行 `OVER (...)`，最终仅保留**当前窗口帧内**贡献数 `n_window > 0` 的端点。 |
| 隐式单值嵌套 `per` 坍缩 & `Attribute` `GROUPING SETS` | L6 + T1/T2 | `count.per(f).sum`、`sum.per(f).sum`（`nulls="ignore"`、精确算术）、同名配对 `min.per(f).min` 与 `max.per(f).max`（不含 `min.per(f).max`）仅对**隐式单值字段样本**（无空样本伪造 `0`）成立；`Attribute` 单次扫描要求新分类为单值全映射且满足精确 L6。 |
| `in_population(P₁ × P₂)` 因式分解 & L3 冗余回查消除 | 独立匹配乘积律 + L3 | 仅当各轴组匹配相互独立（`M(f) = π₁(M(f)) × π₂(M(f))`，无共享多值扇出前缀）时才可拆为独立半连接/时间范围谓词；L3 消除回查须证明关系在原集合上左全且同读同版本；标量 `classify` 后置到 `Population.where` 之后须保证被滤键上 `classify` 无错误。 |
| L7 普通键谓词与 `Frame.fix` 穿透 `GROUP BY` 下推 | L7 + 检查义务分离 | 谓词仅引用普通分组键且满足精确 collation 语义，原全量检查（`nulls="error"`、覆盖、唯一性）仍在未过滤域上独立保留；当存在显式输出域 `by=P`、`by=()` 或独立分支广播时，须**同步用同一谓词裁剪输出域 `K`**；对含 `MetricWindow` 的查询，**普通轴**键谓词可下推至事实表，**时间轴**谓词仅可收缩窗口求值端点而不可截断历史贡献。 |
| 终端融合扩展至 `Frame.reduce` / `over` / `fix` / 同源 `align` | 拉回 + 前推 + `presence` 代数 | 同源待执行图、合格物理载体与排序规则（浮点 `Top` 须对齐 `NaN` 序），且保留全输入检查摘要与完整键/presence 契约。 |
| `UniqueKeys` 消除退化 `per` / `over` 与 `by=P` 共享覆盖检查 | `UniqueKeys` + 支持域代数 | 退化 `per` 坍缩须保证每输出键恒有 `|Jₖ| = 1`（或空样本语义满足 `ρ_out(∅) = m(∅)`），且外层归约满足单元素恒等 `ρ_out({{x}}) = x`（含 `x = NA` 且载体/单位/检查一致，如 `sum`/`mean`（载体兼容时）/`min`/`max`/`median`/`quantile`，不含 `count`/`count_distinct`/`corr`）；`over(..., by=frame.axes)` 每组大小恒为 1（`row_count ≡ 1`、`sum ≡ col`、`corr ≡ NA`）；同次 `observe(..., by=P)` 中必要键域相同的分支共享一次覆盖反连接。 |

指标与维度的组合、集合与多重集推导式、聚合摘要的经典分类分别提供了这套模型的相关背景。[Yang 等，*A Grammar of Data Analysis*](https://arxiv.org/abs/2511.02156) · [Fegaras，*An Algebra for Distributed Big Data Analytics*](https://www.cambridge.org/core/journals/journal-of-functional-programming/article/an-algebra-for-distributed-big-data-analytics/580E6A327FD3AC8E4F9B0EA0408B434B) · [Gray 等，*Data Cube*](https://arxiv.org/abs/cs/0701155)
