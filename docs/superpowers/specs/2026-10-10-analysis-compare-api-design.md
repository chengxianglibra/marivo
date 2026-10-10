# Analysis compare / ratio 公共 API 重设计

Date: 2026-10-10

Status: W0 已实施并提交；W1 已实施并通过本地验收，当前工作区未提交；W2、W3 后续另行授权实施。

Design baseline: `main@e3650922d3`. W1 implementation baseline: `main@da65e5b15e`（修订前工作区干净，包含 W0）。

W1 实施计划：[Analysis compare API W1](../plans/2026-10-10-analysis-compare-api-w1-implementation.md)。

把 `compare` 与 `ratio` 的公共参数按[分析代数](../../specs/analysis/algebra-theory.md)
第 6.7.3 与 8.2 节的结构重新划分：比较设计 c 由端点定义推导；对应关系 a 是一个值，
内含键函数（含时间坐标的映射 τ）与覆盖政策。`compare` 固定产出两个独立的值算子 f：
绝对变化与相对变化，由一个 `ComparisonResult` 的类型化视图承载，不再由调用参数选择。

- W0 值算子对非 Defined 操作数按单元格传播，不再整体拒绝（5.3）。
- W1 统一公共形态并引入双视图比较结果；共享对应关系，保留数值政策，改变比较结果编码与定义指纹。
- W2 让时间键参与覆盖政策：period 比较的输出时间坐标改为对齐键，允许时间键缺侧。
- W3 定义星期、节假日对齐的统一 τ 族，作为 semantic 与 analysis 两份规范的唯一契约。

## 1. 问题

W1 前的写法：

```python
relative = current.compare(baseline, value="relative_change")
union = current.compare(
    baseline, design=mv.TimeChange(pairing=mv.UnionKeys(missing="keep"))
)
yoy = current.compare(prior, design=mv.PeriodChange(alignment=mv.window_bucket()))
quotient = current.ratio(reference, pairing=mv.ExactKeys())
```

代数上 `compare(x, y) = f(x∘ℓ, y∘r)` 定义在对应域 `(K, ℓ, r)` 上，并受设计 c 的准入约束。
当前 API 的问题都来自轴的放置：

| # | 问题 | 位置 |
|---|---|---|
| P1 | 对应关系 a 嵌在设计 c 内部；`ratio` 又把同一概念放在顶层，同一能力有两个入口 | `TimeChange(pairing=)` 与 `ratio(pairing=)` |
| P2 | 设计 c 需要调用方重复声明，但它已由端点定义唯一确定（见第 3 节） | `design=` |
| P3 | 时间桶映射与非时间配对分属不同对象，时间键不参与覆盖政策，也无法扩展到星期、节假日对齐 | `PeriodChange(alignment=)` 与 `pairing=` |
| P4 | 一次比较只能预选一种变化，调用方需重复声明相同端点与对应关系才能获得两种结果 | `value=` |
| P5 | 命名面向实现；跳过精确对应校验增加误用入口 | `UnionKeys`、`metric_empty`、`verification="assume"` |
| P6 | 两份规范对时间桶对齐的能力描述不一致（第 8.1 节） | `semantic-object-model.md:847` 与 `timezone-and-calendar-design.md` |

## 2. 设计原则

1. 调用方只提供不能从端点推导的对应关系；一次比较固定包含绝对与相对变化。
2. 对应关系只有两个独立关注点：键函数决定哪一行对应哪一行（错位问题），
   覆盖政策决定配对键只在一侧出现时怎么办（遗漏问题）。时间坐标是复合键的一个分量，
   不是单独的轴。
3. 配对域 K 由共同键标识（理论 6.7.3），两侧原坐标作为投影保留。
4. 能从已冻结定义唯一推导的事实不再要求调用方声明；不能唯一推导时拒绝，不猜测。
5. 默认保留缺侧，不引入填值假设；采用原 Metric 空集规则与选择时间桶映射必须显式给出。
6. 方法按“比较什么”划分：
   - `compare`：同一个量模板，恰好一类条件不同。
   - `ratio`：两个不同的量，同一条件。
7. 闭合、按变体分派的值类型优先于带可选字段的单类（AGENTS.md）。
8. 精确对应固定使用 checked 政策：适用声明与冻结定义推导仍被信任，未知对应关系执行校验；
   不开放本次调用跳过校验的参数。

## 3. 比较设计的推导

### 3.1 依据

`graph_composition.py` 的 `validate_time_comparison` 已在执行前检查两个端点：
`comparison_bindings` 把每个端点归结为 `(目标成员节点 T, 时间绑定 S)` 捕获序列，
`comparison_template` 给出递归量模板。现有三种设计的准入条件：

| 设计 | 量模板 | 目标成员 T | 时间绑定 S | 其他 |
|---|---|---|---|---|
| time | 相同 | 相同 | 不同 | — |
| period | 相同（剥离 `TimeProduct` 后） | 相同（剥离后） | 不同 | 完整桶数相等（W2 放宽） |
| cohort | 相同 | 不要求 | 相同 | 域为 Group/Singleton，坐标键相同 |

time/period 要求 S 不同，cohort 要求 S 相同，二者互斥。显式选择设计不提供新信息：
选错只会被拒绝。

### 3.2 规则

量模板与端点合法性检查沿用现有规则，同时要求两侧满足绝对变化与相对变化的物理类型准入。
设计推导只决定传给现有校验器的 `design` 值：

| 时间绑定 S | 两侧保留时间网格 | 推导 | `align.time` |
|---|---|---|---|
| 不同 | 否 | `time` | 必须为 `None` |
| 不同 | 是 | `period` | 必填 |
| 相同 | 任意 | `cohort` | 必须为 `None`；目标成员必须不同 |

随后调用现有 `validate_time_comparison(current, baseline, design)`，其余条件按原规则检查。

拒绝情形：

- S 不同且 T 不同：两个条件同时变化。错误列出两侧各自不同的 T 与 S。
- S 相同且 T 相同：两个端点没有差异，没有业务意义，拒绝。当前 `CohortContrast` 允许该自比较，本设计收紧。
- S 相同但域不是 Group/Singleton：沿用现有拒绝，错误说明 Entity 行不能按人群配对。
- 推导为 `period` 但 `align.time` 为 `None`：错误说明需要显式选择时间桶映射并列出可选构造。
- 推导为 `time` 或 `cohort` 但给了 `align.time`：错误说明端点没有可映射的时间网格。

cohort 两侧即使保留时间网格，时间绑定也相同，时间键按普通坐标精确配对，不需要 τ。
实施时须用测试确认这一点。

### 3.3 与理论的关系

理论 8.2 要求比较具有声明意图的设计 c，且不能只凭字段名相同决定。推导满足这一要求：

- 意图由调用方在构造端点时写出（不同的 `time_scope` 或不同的人群选择），推导读取的是冻结节点身份，不是字段名。
- c 照常写入图节点的 `design` 参数，参与定义指纹。
- 两类条件都变或都不变时拒绝，不做猜测。

理论 8.2 增补一句：当两端点恰有一类条件不同时，c 由端点定义唯一确定，无需另行声明。

### 3.4 可见性

推导出的设计必须出现在 `LogicalComparisonResult` / `MaterializedComparisonResult`
的有界单行 `__repr__` 中，连同结果身份与 `.show()` 指引。
`.show()` 首行同时显示实际不同的捕获，例如 `cohort contrast: <target A> vs <target B> at <time role>`。
这用于暴露唯一一种静默误用：想做时间比较却忘了改 baseline 窗口，同时改了人群。
本设计不为此增加可选断言参数。

## 4. 目标 API

```python
import marivo.analysis as mv

comparison = current.compare(baseline)
delta = comparison.absolute
pct = comparison.relative
union = current.compare(baseline, align=mv.outer())
daily = current.compare(prior, align=mv.exact(time=mv.ordinal()))
daily_union = current.compare(prior, align=mv.outer(time=mv.ordinal()))
cohort = paid.compare(free)

result = comparison.execute()
result.show()
allocation = result.absolute.attribute(axes=(region,))
increases = result.relative.where(result.relative.value.gt(0.1))

aov = revenue.ratio(orders, align=mv.exact())
linked = left.ratio(
    right, align=mv.one_to_one(left=left, right=right, via=relationship, time=mv.ordinal())
)
```

### 4.1 `compare`

```python
def compare(
    self,
    baseline: NumericRelation,
    *,
    align: ExactAlignment | OuterAlignment = _EXACT,
) -> LogicalComparisonResult: ...
```

| 参数 | 轴 | 含义 | 替代 |
|---|---|---|---|
| `align` | a | 复合键上的对应关系：键函数（含可选时间映射 τ）与覆盖政策 | `design=` 内的 `pairing=` 与 `alignment=` |

`compare` 不接受 `change` 或 `value` 参数。它返回一个双量结果，两个视图始终存在：

| 视图 | Logical 类型 | Materialized 类型 | 数值语义 |
|---|---|---|---|
| `.absolute` | `LogicalDifferenceRelation` | `MaterializedDifferenceRelation` | `current - baseline`，保留原单位与绝对变化的数值类型 |
| `.relative` | `LogicalNumericRelation` | `MaterializedNumericRelation` | `(current - baseline) / abs(baseline)`，无量纲；零基线为 `Undefined("zero_baseline")` |

`relative` 的分母取 `abs(baseline)`，沿用现行为。结果的符号只由 `current - baseline`
决定，增长为正、下降为负，与基线正负无关；若分母取 `baseline`，负基线时符号反转。
Help 须给出该公式与一个负基线例子：baseline `-100`、current `-50` 得 `+0.5`（亏损收窄）。

#### 结果容器与续接

- `LogicalComparisonResult` 提供 `.absolute`、`.relative`、`execute()`、有界 `show()`
  与 `contract()`；logical 展示只读取冻结定义，不查询业务数据。
- `MaterializedComparisonResult` 提供两视图、有界 `show(n=..., max_output_bytes=...)`、
  隔离的 `to_pandas()`、`contract()` 与既有 Artifact 状态/证据读取。
  访问视图只绑定同一已提交 Artifact 的投影，不分配 Run，不重新计算。
- 容器不作为 `NumericRelation`，不提供 `.value`、`aggregate`、`rank`、`where`、
  `attribute`、`compare` 或 `ratio`。调用方通过明确的视图进入数值续接。
- `.absolute` 拥有原绝对 Difference 的续接；归因仍要求 exact、完整原量状态及其余独立准入。
  `.relative` 不获得原量 rollup、share 或归因权威。
- 视图筛选只产生该视图的选后 Relation，不改变原容器或另一视图。需要组合展示时使用既有 `mv.table`。
- 容器属于既有 Logical/Materialized Result 家族；两视图属于现有 Relation 家族。
  根结果没有单一 quantity，两个视图各有自己的 quantity、单位、精确物理类型与能力契约。

#### 同一次比较的执行与 Cell 政策

- 在同一个对应域上配对一次，冻结与保留一份有序端点和对应投影，计算两个独立 Cell；
  不实现成两个独立 source `compare` 后再拼表。
- 两个算子都直接读取原端点，使用精确中间量与各自的最终舍入。
  相对变化不得从已经舍入或存储的绝对变化反推。
- 每行两视图的 tag/reason 独立。例如 baseline `0`、current `10`：
  absolute 为 `Defined(10)`，relative 为 `Undefined("zero_baseline")`，整次比较成功。
- W0 的非 Defined 传播、优先级、缺侧/空集规则分别应用于两视图。
  outer keep 的缺侧两视图均为 `Undefined("missing_side")`；presence 仍保留在对应投影中。
- 同时产出两种量要求同时满足两者的类型约束；例如 Decimal 必须满足绝对变化的同 scale 条件。
  不拓宽现有数值类型，不为无法形成绝对变化的端点提供 relative-only 模式。
- NaN、inf、数值溢出或其他真正的数值违例仍使整个 Run 失败。
  即使其中一个变化可表示，另一个变化溢出也不得发布半个结果；Cell 级 Undefined 与 Run 失败分开处理。
- 共享 logical 节点的实现范围为单次执行。不同 source `execute()` 仍是不同 Run；
  独立检查、primary 与 required parts 的 native 提交不保证一次扫描或事务快照。

#### 展示与导出

`show()` 同时显示 absolute 与 relative 的值及各自 Cell 状态，标明原单位、无量纲公式、
推导设计与不同捕获；沿用现有行数、字节预算和身份隐藏政策。
`to_pandas()` 返回完整键及 `absolute`、`absolute_cell_tag`、`absolute_cell_reason`、
`relative`、`relative_cell_tag`、`relative_cell_reason`，不使用含义不明的 `value` 列。
各视图的 `to_pandas()` 沿用单值 Relation 的 `value` / Cell 列契约与各自实际数值类型。

### 4.2 `ratio`

```python
def ratio(
    self,
    other: NumericRelation,
    *,
    align: ExactAlignment | OneToOneCorrespondence = _EXACT,
) -> LogicalNumericRelation: ...
```

`ratio` 不推导设计：两侧时间角色必须相同，这是方法本身的准入。同时间角色下时间坐标
按普通坐标精确配对，因此 `ratio` 拒绝带 `time` 的 `ExactAlignment`；跨时间网格的比值只能
通过 `one_to_one(..., time=mv.ordinal())` 绑定。`ratio` 继续拒绝 outer 对应关系。

### 4.3 对应关系值

规范构造入口：

```python
def exact(*, time: OrdinalTimeKey | None = None) -> ExactAlignment: ...

def outer(
    *,
    missing: Literal["keep", "empty_value"] = "keep",
    time: OrdinalTimeKey | None = None,
) -> OuterAlignment: ...

def ordinal() -> OrdinalTimeKey: ...
```

| 构造 | 类型 | 语义 | 替代 |
|---|---|---|---|
| `mv.exact(time=None)` | `ExactAlignment` | checked 精确对应：复合键唯一、完整、键像相等，即两投影均为双射；双空合法；未知对应关系执行检查，任一分量缺侧拒绝，不缩成交集 | `ExactKeys(verification="check")`；带 `time` 时替代 checked 的 `PeriodChange` |
| `mv.outer(missing="keep", time=None)` | `OuterAlignment` | 默认复合键并集；缺侧保留为 MissingCoordinate，不补值；可简写为 `mv.outer()` | `UnionKeys(missing="keep")` |
| `mv.outer(missing="empty_value", time=None)` | `OuterAlignment` | 显式采用已证明的原 Metric 空集 finish；可得到 Defined(0)、Null 或 Undefined，沿用现有规则；无合法原量空集 finish 时拒绝；时间键缺侧拒绝，见 4.5 | `UnionKeys(missing="metric_empty")` |
| `mv.one_to_one(left=, right=, via=, time=None)` | `OneToOneCorrespondence` | 声明的一对一 Relationship 绑定到确切有序端点；仅 `ratio` 接受；只支持精确覆盖 | `one_to_one(..., time=PeriodChange(...))` |

`exact` 不接受 `trust` 或 `verification`，新调用固定编码为 `verification="check"`。
这不要求重复检查已有适用声明或冻结定义推导，也不移除这些信任来源；
它只删除调用方为本次精确对应声明 assume 的入口。此前 assume 变体没有新的公共替代。
`missing` 只存在于 outer 变体，默认 `"keep"`；填入 `empty_value` 必须显式写出。
交集配对继续不提供，需要时先显式 Select（理论 6.7.3）。

### 4.4 时间键函数 τ

配对键为复合键 `k_i(u) = (k_nontime(u), τ_i(t(u)))`。τ 把各侧完整时间网格的每个桶
映射到一个对齐键，是键函数的一部分，只回答“哪个桶对应哪个桶”；对应不上时怎么办
由所在对应关系值的覆盖政策决定。

τ 的共同约束：

- 在图构造时对两侧冻结网格求值，结果按桶身份冻结进定义（见 5.2 的编码），执行、lowering 与 fixed continuation 只读取冻结结果，不重新求值，不重读 Catalog。
- 每侧必须单射：同一侧两个桶得到同一对齐键时拒绝。
- 不重编号已过滤的桶；每个端点在每个非时间键下仍须包含其完整网格。

W1/W2 只提供 `mv.ordinal()`（类型 `OrdinalTimeKey`）：对齐键为桶在其原网格内的序号，
替代 `window_bucket()`。它只表示位置对应，不表示星期或财务含义相同。其余 τ 见第 8 节。

### 4.5 时间键与非时间键的缺侧差异

覆盖政策统一作用于复合键，但缺侧来源不同，`empty_value` 的准入也不同：

| 缺侧来源 | 判定 | 含义 | `keep` | `empty_value` |
|---|---|---|---|---|
| 非时间键 | 缺侧网格中存在该对齐键，但该非时间键下无行 | 已观察，无行 | 允许 | 允许（原 Metric 有空集值时） |
| 时间键 | 缺侧网格中不存在该对齐键 | 该桶不在对方观察窗口内 | 允许 | 拒绝 |

判定依据是对齐键是否属于缺侧一侧的网格，而不是该桶是否有源行。网格观察对每个桶都产出
单元格（`graph_preparation.py:281` 起）：count 或空集规则为 `zero` 的 Metric 得 `Defined(0)`，
其他 Metric 得 `Null(empty_contribution)`。因此窗口内无数据的一天在两侧都有坐标，不表现为缺侧。

| 场景 | 判定 | `empty_value` |
|---|---|---|
| 10 月 5 日无订单，两侧窗口都包含该日 | 不缺侧，两侧都有单元格 | 不涉及 |
| 某地区在 baseline 期间无任何行 | 非时间键缺侧 | 允许 |
| `ordinal()` 比较 10 月（31 天）与 9 月（30 天） | 对齐键 31 不在 9 月网格内，时间键缺侧 | 拒绝 |

时间键缺侧只可能来自对方窗口外的桶；把未观察的值填为空集值等同伪造观测
（上例会得到“9 月 31 日为 0”）。拒绝错误须说明这一区别，并给出两种修复：
改用 `keep` 保留缺侧，或调整窗口使两侧桶数一致。

“每个桶都有单元格”对分组网格观察同样成立。2026-10-10 用 j2 fixture 的临时探针
（August 1–4 日网格，source 路线）实测：

| 观察 | 结果 |
|---|---|
| customer × 日，`order_count` | 4 × 4 = 16 行，无行的日为 `Defined(0)` |
| 客户 region × 日 rollup，`order_count` | 3 × 4 = 12 行；east 仅 1 日有数据，south、west 全期无数据，均补齐 |
| 订单 channel × 日 rollup，静态读取与 `at=grid.before_end` 两种 | 1 × 4 = 4 行，补齐 |
| 客户 region × 日 rollup，`revenue`（sum，未声明 empty） | 12 行，无行的日为 `Null(empty_contribution)` |
| 上述 `order_count` rollup 的 period 比较，exact 与 keep | 均成功，12 行 |

因此现有 period 配对的完整桶集合检查（`graph_local_execution.py:1429` 起）不会因某分组某日
无数据而失败，不需要在观察层补齐。

上述探针在 W0 前还发现值层面的问题：Present 的 `Null` 操作数会让整个比较失败，见 5.3。
该问题已由 W0 修复；W1 必须保留两视图各自的传播行为。

### 4.6 `one_to_one`

```python
def one_to_one(
    *,
    left: NumericRelation,
    right: NumericRelation,
    via: Ref[RelationshipKind],
    time: OrdinalTimeKey | None = None,
) -> OneToOneCorrespondence: ...
```

参数名保持 `time`，类型由 `PeriodChange | None` 改为 `OrdinalTimeKey | None`。
内部 `_time` 字段改为存储该值，`ratio` 与 `one_to_one` 中
`design="period" if ... is not None else "time"` 的判定相应改写。
one_to_one 的精确覆盖固定使用 checked，不再从旧 `PeriodChange.pairing` 读取 assume。

### 4.7 范围之外

- `FunnelResult.compare(baseline)` 无参数，语义由 Funnel 合同拥有，不变。
- linear combination / contrast（理论 8.2）。

## 5. 工作包

### 5.1 W1 统一公共形态与双视图结果

范围：第 3、4 节的全部签名、推导、错误、双视图图节点、执行、披露与持久化恢复。
`ratio` 仍是单值 Relation。`compare` 从单值 Difference 改为一个双视图 Result；
不再承诺比较结果的内部编码或根定义指纹不变。

W1 中时间键覆盖仍是现有能力：`period_mapping` 要求两侧完整网格等长并逐个配对
（`graph_composition.py:146`），`outer` 只作用于非时间键。

| 写法 | W1 行为 |
|---|---|
| `exact(time=ordinal())`，网格等长 | 支持，等价 W1 前的 `PeriodChange(..., pairing=ExactKeys())` |
| `outer(missing=..., time=ordinal())`，网格等长 | 支持，等价 W1 前的 `PeriodChange(..., pairing=UnionKeys(missing=...))` |
| 任意 `time=ordinal()`，网格不等长 | 拒绝，与现在相同；W2 起 `outer` 接受 |

W1 沿用现有对应政策和数值算子含义；公共输入映射如下：

| 公共 | 冻结含义 |
|---|---|
| 推导 time / cohort / period | `design="time"/"cohort"/"period"` |
| 一次 `compare` 的两个视图 | 两个固定算子 `difference` / `relative_change`，无用户选择参数 |
| `exact()` | `pairing="exact", verification="check"` |
| `outer(missing="keep")` | `pairing="keep", verification="check"` |
| `outer(missing="empty_value")` | `pairing="metric_empty", verification="check"` |
| `time=mv.ordinal()` | 与推导的 `design="period"` 一起使用现有 `period_mapping` |

#### 图与执行所有权

- 引入闭合的 `CellCompare`、`ComparisonRead` 规则与 `ComparisonPart`。
  `CellCompare` 绑定两个有序端点、推导设计和一个对应关系，拥有两个必备输出 Cell 的定义与物理类型。
  容器 Signature 的 quantity 为 None；`ComparisonRead(field="absolute" | "relative")`
  产生各自单量 Signature。类型与单位从冻结原端点机械推导，不由调用方声明。
- 继续使用统一 method registry、graph compiler、Runtime 与 Store；
  不增加双 Artifact 拼装、Comparison 专用快照或旁路执行器。
- source lowering 与 fixed local 消费方均在一份配对域上完成存在性/空集政策与数值检查，
  再分别调用已有绝对/相对数值内核。原始端点、对应投影和完整原量状态保留一次。
- 根 primary 固定含两个带前缀的 Cell 字段组；浮点误差界按视图分别保留。
  checked 的对应校验和 W0 的有限数值校验服务于同一组确切有序输入，避免为两个视图重复建立等价义务。
- 视图读取按现有 Result 的投影模式进入统一图。absolute 的完整原量状态、
  Subject 映射、端点与对应权威必须支持既有归因/筛选；
  relative 的 Signature 不因容器还保留 absolute 状态而获得原量能力。
- 递归比较模板、端点捕获与归因准入识别新视图，并从冻结 Comparison 节点解析原端点；
  不重读 Catalog，不通过列名猜测量或恢复权威。

#### 指纹、版本与恢复

- 新 Comparison 根和视图有新的定义指纹。对仍准入的 checked 端点，
  验收保证推导设计、配对域、原端点、数值类型/单位、Cell 政策及原有数值结果等价；
  不以旧单值 compare 的根指纹相等作为验收条件。
- 删除 assume 公共入口与新增绝对变化类型要求意味着并非所有旧 relative-only/assume 调用都存在新等价调用。
  普通 checked ratio 及 checked one_to_one 的底层 `CellDerive` 参数与定义指纹仍应保持；
  这不构成旧 Artifact 编码的兼容承诺。
- W1 图快照 schema 从 `marivo.analysis.graph_dag/v7` 升为 `v8`，
  同步 `graph-dag-v8:` 前缀与 shared value-table codec。
  Store generation 保持 9；沿用旧图版本要求源重新执行的规则，不提供旧版本双读或迁移。
- 两 Cell、required parts、Evidence 与成功终态作为一个 Artifact 原子发布。
  `session.artifact(ref)` 恢复 `MaterializedComparisonResult`；
  两视图在 source-offline 的独立进程中可读取和续接。
  完整性校验覆盖两侧 Cell 布局、精确类型、完整键、共享对应及各视图的端点/状态投影。

W1 单独完成后可进入发布准备。本任务完成 W1 后停止；W2 的实施需后续明确授权。

### 5.2 W2 时间键参与覆盖

#### 输出时间坐标

当前编码以 current 桶作为输出时间坐标，把 baseline 桶经 `bucket_mapping` 翻译到 current 桶
（`graph_local_execution.py:1441`、`graph_exchange.py:1066`、`compiler/graph_lowering.py:704` 起）。
这只在 τ 为双射时成立。时间键缺侧可能出现在任一侧，后续星期对齐中两侧同时出现
（10 月有 5 个周四而 9 月只有 4 个，同时 9 月有 5 个周二而 10 月只有 4 个），
baseline 独有的桶没有 current 坐标可用。

按原则 3，period 比较的输出时间坐标改为对齐键：

- 输出键为 `(非时间键, 对齐键)`；对齐键坐标的角色为 `aligned`，不是 `anchor`。
- 两侧原时间坐标作为对应投影保留在既有的 `correspondence__current_key_i` /
  `correspondence__baseline_key_i` 列中；缺侧一侧为空。
- `.show()` 对每行同时显示对齐键与两侧原时间坐标。
- `exact` 与 `outer` 使用同一输出形态，不随覆盖情况变化。
- `one_to_one(..., time=...)` 的比值采用同一形态，仍只允许精确覆盖。

这改变 period 比较的容器、两视图及带 `time` 的 one_to_one ratio 的输出时间形态与定义指纹；
time 与 cohort 比较不受 W2 的时间坐标变化影响。沿用旧版本需要源重新执行的规则，
不提供旧 period Comparison / Difference 的迁移。

#### 编码

`CellCompare` / `CellDerive` 及其 `ComparisonPart` / `CorrespondencePart` 中的冻结
`bucket_mapping: tuple[(current_bucket, baseline_bucket)]`
替换为每侧一份冻结对齐表：

```text
time_keys.current:  tuple[(bucket_identity, aligned_key)]   # 覆盖 current 完整网格
time_keys.baseline: tuple[(bucket_identity, aligned_key)]   # 覆盖 baseline 完整网格
```

- 每侧对齐表必须覆盖该侧完整网格且单射，替代 `core/model.py:1357` 起的双射校验。
- `exact`：两侧对齐键集合相等；`outer`：允许不等；`empty_value` 按 4.5 判定时间键缺侧并拒绝。
- `time_index` 保留，指向输出键中的对齐键位置。
- 对齐键在 W2 中是非负整数；W3 的 τ 引入复合键时，对齐键编码为带 τ 种类前缀的规范字符串，
  使输出坐标保持单一值类型。
- 涉及时间映射的 parts 版本递增；图快照 schema 由 W1 的 `marivo.analysis.graph_dag/v8` 升为 `v9`，
  沿用“旧版本需要源重新执行”的既有规则（`timezone-and-calendar-design.md`）。

#### 消费方

逐一改为读取两侧对齐表并按对齐键配对：

- `core/rules.py:2059`：`bucket_mapping` 必须等于等长 zip 的校验，改为校验两侧对齐表等于对各自冻结网格求值的 τ 结果。
- `core/rules.py:2196` 起：period 下 `SubjectPart` 的匹配与 `CorrespondencePart` 构造。
- `materialization/graph_local_execution.py:1429`：完整网格检查保留，翻译改为两侧都映射到对齐键。
- `materialization/graph_exchange.py:1066`：fixed continuation 的投影校验，改为按两侧对齐表还原原坐标，并校验缺侧行的原坐标为空。
- `compiler/graph_lowering.py:704`、`740`、`790`、`908`、`972`：SQL lowering 的 CASE 映射与完整网格检查，改为两侧各自映射到对齐键后按复合键 full outer 配对。
- `materialization/graph_composition.py:146`、`566` 与 `graph_relation.py:1216`：`period_mapping` 改为产出两侧对齐表，不再要求等长。
- `materialization/graph_snapshot.py`、`graph_value_tables.py`：schema 版本。
- 消费 period Comparison 两视图或 one_to_one ratio 时间坐标的下游方法（按时间粒度上卷、时间序列方法等）：W2 计划第一步列出全部读取 `anchor` 角色的消费方；对齐键不是 `anchor`，依赖该角色的消费方须拒绝或明确改读两侧原时间坐标，不伪装为 current anchor。

### 5.3 W0 非 Defined 操作数按单元格传播

以下记录 W0 前的问题与已实施的修复。核实 4.5 时发现：只要任一侧存在 Present 的非 Defined 单元格，比较整体失败，
报 `dataset.graph_check` / `Expected: finite_numeric`。这与 period、时间键和覆盖政策无关。
2026-10-10 用 j2 fixture 临时探针在 source 路线实测：

| 比较 | 结果 |
|---|---|
| `revenue` customer 月度 time 比较，exact | 失败，1 行违例：客户 D 9 月无订单，得 `Null(empty_contribution)` |
| 同上，`UnionKeys(missing="keep")` 与 `"metric_empty"` | 均失败，同一行违例 |
| `revenue` region × 日 rollup 的 period 比较 | 失败，22 行违例 |
| `order_count` 同形比较 | 成功，count 空集为 `Defined(0)` |

当时的来源是 `CellDerive` 的数值检查 `source.finite_numeric@v1`（`compiler/graph_lowering.py:4331` 起），
它要求每个现存操作数单元格为 Defined。W0 前的契约在 `operators-and-frames.md` 第 335 行：
“The existing side still must pass strict numeric consumption.”

当时的后果：sum 类 Metric 未声明 `empty=ms.empty.zero()` 时，空集结果是 Null。任何分组或网格比较，
只要有一个分组或桶无数据，整个 Run 就失败。这才是“某一天没有数据”场景的真实失败点，
不来自 4.5 的时间键规则。当时唯一绕行是把 Metric 声明为空集取 0，但“无数据即 0”是
业务口径，不能为了让比较通过而修改。

W0 前的契约内部也不一致：同一段规定合成的 Null/Undefined 端点“retains that tag and original
reason in the result (no arithmetic)”，而现存的 Null 端点却让整个 Run 失败。

决定（2026-10-10）：值算子 f 对非 Defined 操作数按单元格传播，不再整体拒绝。

- 任一操作数非 Defined 时不做算术，输出单元格取该操作数的 tag 与 reason。
- 两侧都非 Defined 时按固定优先级取一侧：Unknown > Undefined > Null，并在对应投影中保留两侧原 reason。
- NaN、inf 等真正的数值违例仍整体拒绝。
- W0 已改变 `CellDerive` 的值政策（此前为 `value_policy="strict"`）与输出指纹，`compare` 与 `ratio` 同时适用。

它与 W1/W2 正交，已作为 W0 先于 W1 交付；W1 双视图须分别保留该政策。

## 6. 公共面变化

`marivo.analysis.__all__`：

| 移除 | 新增 | 保留 |
|---|---|---|
| `TimeChange`、`CohortContrast`、`PeriodChange`、`WindowBucketAlignment`、`window_bucket`、`ExactKeys`、`UnionKeys` | `exact`、`outer`、`ordinal`、`ExactAlignment`、`OuterAlignment`、`OrdinalTimeKey`、`LogicalComparisonResult`、`MaterializedComparisonResult` | `one_to_one`、`OneToOneCorrespondence`、现有 Difference / Numeric Relation 类型 |

三个设计类不再有公共替代。三个值类型只供类型标注，规范构造入口是对应工厂，
与现有 `one_to_one` / `OneToOneCorrespondence` 的成对模式一致；均保留有界单行
`__repr__` 与 `.show()`。两个 Result 类型加入现有结果家族，其公共协议见 4.1。
`ComparisonResult` 只是本设计中的家族称呼，不新增同名公共别名；
`CellCompare`、`ComparisonRead`、`ComparisonPart` 保持内部类型。

按 AGENTS.md 的弱依赖规则，不保留旧名别名或兼容 shim。旧调用以 Python 原生
`TypeError`（未知关键字 `design` / `value` / `change` / `pairing` / `trust` / `verification`）失败；`one_to_one(time=PeriodChange(...))`
因 `PeriodChange` 不再导出而以 `AttributeError` 失败。Help 目标与指南给出新写法。

## 7. 窗口汇总值的日历对齐

只比较窗口汇总值时（端点不保留时间网格，推导为 `time`），节假日与同星期对齐由 baseline
的 `time_scope` 选择表达，不需要 τ：

- 节假日：`catalog.temporal_sets.get(...).occurrence(key)` 取对应那次的 `TimeScope`。
- 同星期：baseline 窗口取 current 窗口平移 364 天。

## 8. W3 统一时间对齐契约

### 8.1 不一致

`docs/specs/semantic/semantic-object-model.md:847` 写道，累计派生指标的可比周期结果
“may align by window ordinal, DOW position, holiday position, or holiday-then-DOW position”。
analysis 规范（`timezone-and-calendar-design.md` Period correspondence 节）与实现只有 ordinal，
且没有任何公共参数能选择后三种。semantic 侧描述的是目标能力，不是当前契约。

### 8.2 统一方案

1. 唯一所有者：时间桶对齐只由 analysis 比较契约拥有，即 `align` 的 `time=` 字段。
   适用于所有 period 比较，累计指标不另设对齐机制。
2. semantic 规范只保留累计派生指标的比较准入（外层分量均为累计、anchor 兼容），
   删除对齐方式的描述，改为引用 analysis 比较契约。W1 文档更新时一并修改该句。
3. τ 是闭合族，四个成员与 semantic 原句列举的四种方式一一对应，定义只写在 analysis 规范：

| 构造 | 对应原句 | 对齐键 | 依赖 | 状态 |
|---|---|---|---|---|
| `mv.ordinal()` | window ordinal | 桶在原网格内的序号 | 无 | W1/W2 |
| `mv.weekday()` | DOW position | `(星期几, k)`，k 为该桶在本侧网格中同一星期几的序次 | 网格冻结的日历与时区权威 | W3 |
| `mv.holiday(current=, baseline=)` | holiday position | 桶首日相对该侧节假日 occurrence 首日的有符号天数 | `TemporalSet` occurrence | W3 |
| `mv.holiday_then_weekday(current=, baseline=)` | holiday-then-DOW position | occurrence 内：`("holiday", 偏移)`；occurrence 外：`(前/后, 星期几, k)`，k 从 occurrence 向外计数 | `TemporalSet` occurrence 与日历权威 | W3 |

4. 共同规则：
   - `weekday`、`holiday`、`holiday_then_weekday` 要求日粒度网格，其他粒度拒绝。
   - `holiday` 与 `holiday_then_weekday` 的 `current=` / `baseline=` 各接受一个 occurrence，
     其 `TemporalSet` 快照摘要进入定义指纹。`occurrence(key)` 当前返回的 `TimeScope` 是否携带
     快照摘要尚未核实；若没有，W3 补充。
   - 所有 τ 都按 4.4 在构造时求值，编码为 5.2 的两侧对齐表。因此 W3 不改变执行、lowering、
     恢复与覆盖政策，只新增 τ 构造与求值。
   - 两侧窗口长度或节假日天数不同造成的时间键缺侧，统一由 W2 的覆盖政策处理，τ 内部不截断、不补齐。
5. 工作日序号（依赖 `WorkSchedule`）不在统一族内，有需求时另行提案。

## 9. 受影响文件

W1 实现：

- `marivo/analysis/_comparison.py`：删除三个设计类、`WindowBucketAlignment`/`window_bucket`；`ExactKeys`/`UnionKeys` 改为带 `time` 字段的 `ExactAlignment`/`OuterAlignment`；新增 `OrdinalTimeKey` 及三个工厂。
- `marivo/analysis/public_dsl.py`：新签名、两个 Comparison Result 类型、类型化视图、跨 Session 所有权、`execute`、`repr/show/contract` 与 `OneToOneCorrespondence`。
- `marivo/analysis/materialization/graph_composition.py`、`graph_relation.py`：设计推导、复合结果构造、视图续接、递归模板/捕获；增加 cohort 自比较拒绝与两条件同时变化的错误。
- `marivo/analysis/core/model.py`、`core/rules.py`、`methods/comparison.py`、`methods/semantics.py`、`methods/builtin.py`、`methods/registry.py`：闭合规则/part、两个视图的 Signature、对应与原量状态权威、精确类型准入与统一 method 注册。
- `marivo/analysis/compiler/graph_plan.py`、`graph_lowering.py`、`graph_attribution.py`：source 共用配对域、两 Cell lowering、required parts 与 absolute 的归因准入。
- `marivo/analysis/materialization/graph_source_execution.py`、`graph_local_execution.py`、`graph_exchange.py`、`graph_attribution.py`：source 提交、fixed 双视图、投影与证据校验、完整原量状态续接。
- `marivo/analysis/materialization/graph_dataset.py`、`graph_snapshot.py`、`graph_value_tables.py`：双 Cell 展示/导出、恢复 kind 与完整性、shared codec 的 `graph_dag/v8`。
- `marivo/analysis/__init__.py`、`marivo/analysis/_public.py`：导出。

W2 实现：见 5.2 消费方列表。

披露与 Help：

- `marivo/analysis/operators/_disclosure.py`、`marivo/analysis/session/_public_disclosure.py`：Help 目标、示例与预算；`relative` 公式与负基线例子。
- 公共 Result 与视图的 native 披露、动态 continuation、独立 reachability/drift/budget 测试：根容器只披露本身操作，数值能力归各视图。
- 错误引导中出现 `mv.TimeChange()`、`mv.PeriodChange(alignment=mv.window_bucket())` 的 `_reject` 提示。

测试：

- `tests/surface/exports.py`、`tests/surface/analysis_context_baseline.json`、`tests/surface/test_analysis_imports.py`。
- `tests/typing/analysis_dsl_public_contract.py`。
- `tests/analysis/numeric/test_analysis_comparison.py`、`test_analysis_comparison_runtime.py`、`test_comparison_non_defined.py`、`test_comparison_consumers.py`、`test_analysis_references.py`、`test_analysis_attribution_runtime.py`。
- `tests/analysis/graph/test_disclosure.py`、`test_domain_contract.py`、`test_premise_verification.py`。
- `tests/analysis/materialization/test_comparison_recovery.py`、`comparison_recovery_worker.py`、`test_graph_dataset_reads.py`、`tests/analysis/statistics/deviation_contribution_worker.py`、`tests/packaging/relation_journeys.py`。
- 共享 schema 版本断言与所有受影响的 compare 调用点；slow 恢复与披露预算测试需显式运行。

实施前重新核对 HEAD 与工作区，保留任务外改动；本节行号只是历史定位，不作为修改依据。

文档：

- 理论：`docs/specs/analysis/algebra-theory.md` 第 8.2 节增补推导说明（第 3.3 节）。
- 规范：
  - `docs/specs/analysis/operators-and-frames.md`（293、328 行附近）、`python-analysis-design.md`、`session-state-and-runtime.md`、`docs/specs/temporal-semantics.md`：双视图所有权、独立 Cell、类型准入与当前图版本。
  - `timezone-and-calendar-design.md` Period correspondence 节：W1 改名并明确等长/current 时间坐标不变；W2 改写完整桶规则，写入对齐键输出与 `graph_dag/v9`；W3 契约定义写入同一节。
  - `docs/specs/semantic/semantic-object-model.md:847`：按 8.2 第 2 条修改。
- 站点（中英同步，仅 latest）：`site/src/content/docs/docs/latest/guides/comparison-and-attribution.mdx`、`reference/troubleshooting.mdx` 及 `site/src/content/docs/zh-cn/docs/latest/` 对应文件；其余旧调用点同步。比值示例使用两个不同量。
- `v0.1`–`v0.5` 版本化文档、`site/dist`、`site/public/api` 生成物不手改。

Skills：`marivo/skills` 当前不引用上述符号，预计无需修改；若实施中发现间接依赖，修改前另行请求批准。

## 10. 实施顺序

W0（已完成；以下为交付记录）：

1. 先写失败测试：4.5 与 5.3 探针中的 `revenue` time 与 period 比较（exact、keep、metric_empty），断言 Null 单元格按单元格传播、其余单元格正常计算；两侧都非 Defined 时的优先级；`ratio` 同样适用；NaN/inf 仍整体拒绝。
2. 改造 `CellDerive` 值政策与 `source.finite_numeric@v1` 检查，local 与 SQL lowering 两条路径一致；fixed continuation 与冷恢复下结果一致。
3. 更新 `operators-and-frames.md` 第 335 行附近的契约与 Help 中相关表述。
4. W1 开工前重跑必要的传播回归，不重新实施 W0。

W1：

1. 冻结当前基线：仍准入的 {time, cohort, period} × {exact checked, outer keep, outer empty_value} 记录两个旧算子的数值、类型、单位、Cell、原端点与对应；checked ratio / one_to_one 另记录底层定义指纹。assume 与旧 relative-only 不能作为全量等价矩阵。
2. 更新公共值、推导、两个 Result 与内部闭合规则；锁定新签名、跨 Session 拒绝、第 3.2 节的拒绝情形、cohort 保留同一时间网格及 W1 不等长拒绝。
3. 实现 source/fixed 共用配对域、两视图独立算术/Cell 与 absolute 的原量状态续接；验证零/负基线、Decimal、overflow、非 Defined 与缺侧。
4. 更新 shared codec 为 `graph_dag/v8`，实现单 Artifact 原子发布、投影完整性与 source-offline 冷恢复；source、fixed、cold 三类证据分开验收。
5. 迁移所有调用点与 public typing；更新 `__all__`、native Help、动态 guidance、repr/show、预算与独立 surface 测试，不保留旧入口。
6. 更新理论、规范（含 semantic 847 行）与站点 latest 中英文档。
7. 先跑窄范围测试，再跑定向 slow、受影响 Runtime 与 `make check-agent`；具体入口及完成条件见 [W1 实施计划](../plans/2026-10-10-analysis-compare-api-w1-implementation.md)。

W2（W1 完成后另行授权）：

1. 前置核实：
   - 列出全部读取 period Comparison 两视图及 one_to_one ratio 的 `anchor` 时间坐标消费方，确定逐个拒绝或改读原坐标。
   - 分组网格观察为每个分组补齐全部桶的单元格，已于 2026-10-10 核实（见 4.5）。
     增加测试锁定该行为：count、`zero` 空集规则、`null` 空集规则各一例，分组中包含全期无数据的分组。
     `null` 一例须保留已实施的 W0 传播，分别断言两视图。
2. 先写失败测试：不等长网格的 `outer(keep)`（current 更长、baseline 更长）、`empty_value` 遇时间键缺侧的拒绝、`exact` 不等长的拒绝、fixed continuation 与冷恢复下的原坐标还原、local 与 SQL lowering 两条执行路径结果一致。
3. 实现两侧对齐表编码、相关 parts 版本与 `graph_dag/v9`，按 5.2 改造各消费方。
4. 更新 period 相关测试的期望输出形态。
5. 更新 `timezone-and-calendar-design.md` 与站点文档。
6. 验收同 W1；period 比较涉及 Runtime 的部分用 `make runtime-test TESTS='...'` 跑受影响的文件。

W3：在 W2 合入后单独出实施计划。

## 11. 已决事项

- 精确对应固定 checked；删除 `trust` / `verification` 配置入口，适用声明与冻结推导继续受信任。
- `outer` 的 `missing` 默认 `"keep"`；`"empty_value"` 必须显式选择。
- `compare` 无 `change` / `value` 参数，固定返回 absolute / relative 双视图；容器不是单量 Relation。
- `relative` 分母保持 `abs(baseline)`，结果符号即变化方向（4.1）。
- 两视图从原操作数独立计算，Cell 状态独立；两者共同类型准入，任一数值溢出都使整次 Run 失败。
- 新 compare 根/视图指纹改变，checked ratio 的底层规则保持；W1 图版本 v7 → v8，W2 v8 → v9，无旧版本双读或迁移。
- 同端点 cohort 自比较拒绝（3.2）。
- 时间桶对齐由 analysis 比较契约唯一拥有，τ 族闭合为四个成员（8.2）。
- 本任务仅 W1；保留等长网格与 current 桶输出，W2/W3 后续另行实施。
- 非 Defined 操作数按单元格传播，两侧都非 Defined 时优先级 Unknown > Undefined > Null，作为 W0 先于 W1 交付（5.3）。

## 12. 待决问题

- W2 的输出形态改变对下游消费方的实际影响，以 W2 第 1 步的清单为准；若有消费方必须保留 `anchor` 时间坐标，需在该步决定方案。
