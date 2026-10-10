# Analysis compare / ratio 公共 API 重设计

Date: 2026-10-10

Status: W0 已实施（2026-10-10，未提交）；W1、W2 未实施，依次交付；W3 只定义统一契约，另行实施。

Baseline: `main@e3650922d3`.

把 `compare` 与 `ratio` 的公共参数按[分析代数](../../specs/analysis/algebra-theory.md)
第 6.7.3 与 8.2 节的结构重新划分：比较设计 c 由端点定义推导；对应关系 a 是一个值，
内含键函数（含时间坐标的映射 τ）与覆盖政策；值算子 f 单独一个参数。

- W0 值算子对非 Defined 操作数按单元格传播，不再整体拒绝（5.3）。
- W1 统一公共形态，内部编码与定义指纹不变。
- W2 让时间键参与覆盖政策：period 比较的输出时间坐标改为对齐键，允许时间键缺侧。
- W3 定义星期、节假日对齐的统一 τ 族，作为 semantic 与 analysis 两份规范的唯一契约。

## 1. 问题

当前写法：

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
| P4 | 值算子 f 用字符串选择，名字不说明它计算的是变化量 | `value=` |
| P5 | 命名面向实现：`UnionKeys`、`metric_empty`、`verification="assume"` | `_comparison.py` |
| P6 | 两份规范对时间桶对齐的能力描述不一致（第 8.1 节） | `semantic-object-model.md:847` 与 `timezone-and-calendar-design.md` |

## 2. 设计原则

1. 一个轴一个顶层参数，参数值不嵌套另一个轴。
2. 对应关系只有两个独立关注点：键函数决定哪一行对应哪一行（错位问题），
   覆盖政策决定配对键只在一侧出现时怎么办（遗漏问题）。时间坐标是复合键的一个分量，
   不是单独的轴。
3. 配对域 K 由共同键标识（理论 6.7.3），两侧原坐标作为投影保留。
4. 能从已冻结定义唯一推导的事实不再要求调用方声明；不能唯一推导时拒绝，不猜测。
5. 业务口径决定的选择（缺侧政策、时间桶映射）必须显式给出，不提供默认值。
6. 方法按“比较什么”划分：
   - `compare`：同一个量模板，恰好一类条件不同。
   - `ratio`：两个不同的量，同一条件。
7. 闭合、按变体分派的值类型优先于带可选字段的单类（AGENTS.md）。

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

量模板与端点合法性检查不变。推导只决定传给现有校验器的 `design` 值：

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

推导出的设计必须出现在 `LogicalDifferenceRelation` 的 `__repr__` 与 `.show()` 首行，
连同实际不同的捕获，例如 `cohort contrast: <target A> vs <target B> at <time role>`。
这用于暴露唯一一种静默误用：想做时间比较却忘了改 baseline 窗口，同时改了人群。
本设计不为此增加可选断言参数。

## 4. 目标 API

```python
import marivo.analysis as mv

delta = current.compare(baseline)
pct = current.compare(baseline, change="relative")
union = current.compare(baseline, align=mv.outer(missing="keep"))
daily = current.compare(prior, align=mv.exact(time=mv.ordinal()))
daily_union = current.compare(prior, align=mv.outer(missing="keep", time=mv.ordinal()))
cohort = paid.compare(free)

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
    change: Literal["absolute", "relative"] = "absolute",
    align: ExactAlignment | OuterAlignment = _EXACT,
) -> LogicalDifferenceRelation: ...
```

| 参数 | 轴 | 含义 | 替代 |
|---|---|---|---|
| `change` | f | `absolute`：`current - baseline`；`relative`：`(current - baseline) / abs(baseline)`，零基线为 `Undefined("zero_baseline")` | `value="difference"/"relative_change"` |
| `align` | a | 复合键上的对应关系：键函数（含可选时间映射 τ）与覆盖政策 | `design=` 内的 `pairing=` 与 `alignment=` |

`relative` 的分母取 `abs(baseline)`，沿用现行为。结果的符号只由 `current - baseline`
决定，增长为正、下降为负，与基线正负无关；若分母取 `baseline`，负基线时符号反转。
Help 须给出该公式与一个负基线例子：baseline `-100`、current `-50` 得 `+0.5`（亏损收窄）。

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

| 构造 | 类型 | 语义 | 替代 |
|---|---|---|---|
| `mv.exact(trust="checked", time=None)` | `ExactAlignment` | 复合键唯一、完整、键像相等，即两投影均为双射；双空合法；重复键或任一分量缺侧均拒绝，不缩成交集 | `ExactKeys(verification="check")`；带 `time` 时替代 `PeriodChange(alignment=window_bucket(), pairing=ExactKeys())` |
| `mv.exact(trust="declared", time=None)` | `ExactAlignment` | 键相等由调用方担保，本次不检查；适用声明与推导仍被信任 | `ExactKeys(verification="assume")` |
| `mv.outer(missing="keep", time=None)` | `OuterAlignment` | 复合键并集；缺侧保留为 MissingCoordinate，不补零 | `UnionKeys(missing="keep")` |
| `mv.outer(missing="empty_value", time=None)` | `OuterAlignment` | 复合键并集；缺侧取原 Metric 的空集值（需已证明，如 count/sum 为 0；无空集值的 Metric 拒绝）；时间键缺侧拒绝，见 4.5 | `UnionKeys(missing="metric_empty")` |
| `mv.one_to_one(left=, right=, via=, time=None)` | `OneToOneCorrespondence` | 声明的一对一 Relationship 绑定到确切有序端点；仅 `ratio` 接受；只支持精确覆盖 | `one_to_one(..., time=PeriodChange(...))` |

`trust` 只存在于 exact 变体，`missing` 只存在于 outer 变体且必填，两者不合并为一个带可选字段的类。
交集配对继续不提供，需要时先显式 Select（理论 6.7.3）。

`trust` 改名的理由：`assume` 在理论 11.2 中是一条信任来源声明，而不是配对方式；
`declared` 让调用点直接读出“这是我担保的”。

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

但值层面存在另一个问题：Present 的 `Null` 操作数会让整个比较失败，见 5.3。
上表 `revenue` 的 period 比较即因此失败。

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

### 4.7 范围之外

- `FunnelResult.compare(baseline)` 无参数，语义由 Funnel 合同拥有，不变。
- linear combination / contrast（理论 8.2）。

## 5. 工作包

### 5.1 W1 统一公共形态

范围：第 3、4 节的全部签名、推导与错误；内部编码不变。

W1 中时间键覆盖仍是现有能力：`period_mapping` 要求两侧完整网格等长并逐个配对
（`graph_composition.py:146`），`outer` 只作用于非时间键。

| 写法 | W1 行为 |
|---|---|
| `exact(time=ordinal())`，网格等长 | 支持，等价现在的 `PeriodChange(..., pairing=ExactKeys())` |
| `outer(missing=..., time=ordinal())`，网格等长 | 支持，等价现在的 `PeriodChange(..., pairing=UnionKeys(missing=...))` |
| 任意 `time=ordinal()`，网格不等长 | 拒绝，与现在相同；W2 起 `outer` 接受 |

W1 公共参数到现有内部编码的映射是全函数，内部节点参数取值集合不变：

| 公共 | 内部 `combine` 参数 |
|---|---|
| 推导 time / cohort / period | `design="time"/"cohort"/"period"` |
| `change="absolute"/"relative"` | 方法 `"difference"/"relative_change"` |
| `exact(trust="checked")` | `pairing="exact", verification="check"` |
| `exact(trust="declared")` | `pairing="exact", verification="assume"` |
| `outer(missing="keep")` | `pairing="keep", verification="check"` |
| `outer(missing="empty_value")` | `pairing="metric_empty", verification="check"` |
| `time=mv.ordinal()` | 与推导的 `design="period"` 一起使用现有 `period_mapping` |

除第 3.2 节的同端点 cohort 自比较新拒绝外，所有现可构造的比较在 W1 新写法下得到相同的
`design` 与定义指纹。这一结论来自阅读校验与构造代码，实施第一步须用测试证明。
W1 单独合入后即可发布；W2 紧随其后实施。

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

这改变 period 比较（compare 与带 `time` 的 one_to_one ratio）的输出形态与定义指纹；
time 与 cohort 比较不受影响。现有 `compare` 契约已规定“恢复旧 Difference 需要重新执行
其源比较”，因此不提供旧 period Difference 的迁移。

#### 编码

`CellDerive` / `CorrespondencePart` 的 `bucket_mapping: tuple[(current_bucket, baseline_bucket)]`
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
- `CorrespondencePart` 版本递增；图快照 schema 由 `marivo.analysis.graph_dag/v7` 升为 `v8`，
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
- 消费 period Difference 输出时间坐标的下游方法（按时间粒度上卷、时间序列方法等）：W2 计划第一步列出全部读取 `anchor` 角色的消费方；对齐键不是 `anchor`，这些消费方遇到 period Difference 时拒绝，错误指向两侧原时间坐标。

### 5.3 W0 非 Defined 操作数按单元格传播

核实 4.5 时发现：只要任一侧存在 Present 的非 Defined 单元格，比较整体失败，
报 `dataset.graph_check` / `Expected: finite_numeric`。这与 period、时间键和覆盖政策无关。
2026-10-10 用 j2 fixture 临时探针在 source 路线实测：

| 比较 | 结果 |
|---|---|
| `revenue` customer 月度 time 比较，exact | 失败，1 行违例：客户 D 9 月无订单，得 `Null(empty_contribution)` |
| 同上，`UnionKeys(missing="keep")` 与 `"metric_empty"` | 均失败，同一行违例 |
| `revenue` region × 日 rollup 的 period 比较 | 失败，22 行违例 |
| `order_count` 同形比较 | 成功，count 空集为 `Defined(0)` |

来源是 `CellDerive` 的数值检查 `source.finite_numeric@v1`（`compiler/graph_lowering.py:4331` 起），
它要求每个现存操作数单元格为 Defined。现行契约在 `operators-and-frames.md` 第 335 行：
“The existing side still must pass strict numeric consumption.”

后果：sum 类 Metric 未声明 `empty=ms.empty.zero()` 时，空集结果是 Null。任何分组或网格比较，
只要有一个分组或桶无数据，整个 Run 就失败。这才是“某一天没有数据”场景的真实失败点，
不来自 4.5 的时间键规则。现有唯一绕行是把 Metric 声明为空集取 0，但“无数据即 0”是
业务口径，不能为了让比较通过而修改。

现行契约内部也不一致：同一段规定合成的 Null/Undefined 端点“retains that tag and original
reason in the result (no arithmetic)”，而现存的 Null 端点却让整个 Run 失败。

决定（2026-10-10）：值算子 f 对非 Defined 操作数按单元格传播，不再整体拒绝。

- 任一操作数非 Defined 时不做算术，输出单元格取该操作数的 tag 与 reason。
- 两侧都非 Defined 时按固定优先级取一侧：Unknown > Undefined > Null，并在对应投影中保留两侧原 reason。
- NaN、inf 等真正的数值违例仍整体拒绝。
- 这改变 `CellDerive` 的值政策（现为 `value_policy="strict"`）与输出指纹，`compare` 与 `ratio` 同时适用。

它与 W1/W2 正交，作为 W0 先于 W1 交付。

## 6. 公共面变化

`marivo.analysis.__all__`：

| 移除 | 新增 | 保留 |
|---|---|---|
| `TimeChange`、`CohortContrast`、`PeriodChange`、`WindowBucketAlignment`、`window_bucket`、`ExactKeys`、`UnionKeys` | `exact`、`outer`、`ordinal`、`ExactAlignment`、`OuterAlignment`、`OrdinalTimeKey` | `one_to_one`、`OneToOneCorrespondence` |

三个设计类不再有公共替代。三个值类型只供类型标注，规范构造入口是对应工厂，
与现有 `one_to_one` / `OneToOneCorrespondence` 的成对模式一致；均保留有界单行
`__repr__` 与 `.show()`。新名在现有导出中无冲突（已检查 `marivo/analysis/__init__.py`、
`_public.py`、`tests/surface/exports.py`）。

按 AGENTS.md 的弱依赖规则，不保留旧名别名或兼容 shim。旧调用以 Python 原生
`TypeError`（未知关键字 `design` / `value` / `pairing`）失败；`one_to_one(time=PeriodChange(...))`
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
- `marivo/analysis/public_dsl.py`：`compare`（约 2268 行）增加推导、`ratio`（约 2316 行）、`OneToOneCorrespondence` / `one_to_one`（约 7598 行）。
- `marivo/analysis/materialization/graph_composition.py`：推导函数放在 `validate_time_comparison` 旁，复用 `comparison_bindings`；增加 cohort 自比较拒绝与两条件同时变化的错误。
- `marivo/analysis/__init__.py`、`marivo/analysis/_public.py`：导出。
- `marivo/analysis/core/rules.py`、`materialization/graph_source_execution.py`：仅更新引用公共类型的位置。
- `LogicalDifferenceRelation` 的 `__repr__` / `.show()`：显示推导设计与不同捕获。

W2 实现：见 5.2 消费方列表。

披露与 Help：

- `marivo/analysis/operators/_disclosure.py`、`marivo/analysis/session/_public_disclosure.py`：Help 目标、示例与预算；`relative` 公式与负基线例子。
- 错误引导中出现 `mv.TimeChange()`、`mv.PeriodChange(alignment=mv.window_bucket())` 的 `_reject` 提示。

测试：

- `tests/surface/exports.py`、`tests/surface/analysis_context_baseline.json`、`tests/surface/test_analysis_imports.py`。
- `tests/typing/analysis_dsl_public_contract.py`。
- `tests/analysis/numeric/test_analysis_comparison_runtime.py`、`test_comparison_consumers.py`、`test_analysis_references.py`、`test_analysis_attribution_runtime.py`。
- `tests/analysis/graph/test_disclosure.py`、`test_domain_contract.py`、`test_premise_verification.py`。
- `tests/analysis/materialization/test_comparison_recovery.py`、`comparison_recovery_worker.py`、`tests/analysis/statistics/deviation_contribution_worker.py`、`tests/packaging/relation_journeys.py`。

工作区中 `marivo/analysis/compiler/graph_lowering.py`、`tests/analysis/numeric/test_analysis_references.py`
与 `tests/analysis/statistics/deviation_contribution_worker.py` 当前有未提交改动，实施前须先确认这些改动的归属。

文档：

- 理论：`docs/specs/analysis/algebra-theory.md` 第 8.2 节增补推导说明（第 3.3 节）。
- 规范：
  - `docs/specs/analysis/operators-and-frames.md`（293、328 行附近）、`python-analysis-design.md`、`docs/specs/temporal-semantics.md`。
  - `timezone-and-calendar-design.md` Period correspondence 节：W1 改名；W2 改写“UnionKeys may union non-time coordinates only after this complete bucket rule succeeds; it cannot truncate, pad or infer missing time buckets”，并写入对齐键输出与 `graph_dag/v8`；W3 契约定义写入同一节。
  - `docs/specs/semantic/semantic-object-model.md:847`：按 8.2 第 2 条修改。
- 站点（中英同步，仅 latest）：`site/src/content/docs/docs/latest/guides/comparison-and-attribution.mdx`、`reference/troubleshooting.mdx` 及 `zh-cn` 对应文件。顺带把 `current.ratio(current, ...)` 示例换成两个不同量的比值。
- `v0.1`–`v0.5` 版本化文档、`site/dist`、`site/public/api` 生成物不手改。

Skills：`marivo/skills` 当前不引用上述符号，预计无需修改；若实施中发现间接依赖，修改前另行请求批准。

## 10. 实施顺序

W0（先于 W1）：

1. 先写失败测试：4.5 与 5.3 探针中的 `revenue` time 与 period 比较（exact、keep、metric_empty），断言 Null 单元格按单元格传播、其余单元格正常计算；两侧都非 Defined 时的优先级；`ratio` 同样适用；NaN/inf 仍整体拒绝。
2. 改造 `CellDerive` 值政策与 `source.finite_numeric@v1` 检查，local 与 SQL lowering 两条路径一致；fixed continuation 与冷恢复下结果一致。
3. 更新 `operators-and-frames.md` 第 335 行附近的契约与 Help 中相关表述。
4. 验收同 W1。

W1：

1. 在基线写法下记录定义指纹：{time, cohort, period} × {exact checked, exact declared, outer keep, outer empty_value} × {difference, relative_change}，以及 `ratio` 的 exact 与 one_to_one（含/不含时间网格）。
2. 实现推导与新签名，确认步骤 1 的指纹全部不变；补充第 3.2 节每个拒绝情形与不等长网格拒绝的测试，包括 cohort 两侧保留时间网格的情形。
3. 更新 `__all__`、Help registry 与预算、错误引导、repr/show，更新 surface 快照。
4. 更新其余测试调用点。
5. 更新理论、规范（含 semantic 847 行）与站点 latest 中英文档。
6. 验收：`make test TESTS='tests/analysis/numeric tests/analysis/graph tests/surface tests/typing'`、`make typecheck`、`make lint-agent`、`make check-agent`。

W2（W1 合入后立即开始）：

1. 前置核实：
   - 列出全部读取 period Difference `anchor` 时间坐标的下游消费方，确定逐个拒绝或改读原坐标。
   - 分组网格观察为每个分组补齐全部桶的单元格，已于 2026-10-10 核实（见 4.5）。
     增加测试锁定该行为：count、`zero` 空集规则、`null` 空集规则各一例，分组中包含全期无数据的分组。
     `null` 一例的比较须在 5.3 落地后才能成功；之前只断言观察输出补齐。
2. 先写失败测试：不等长网格的 `outer(keep)`（current 更长、baseline 更长）、`empty_value` 遇时间键缺侧的拒绝、`exact` 不等长的拒绝、fixed continuation 与冷恢复下的原坐标还原、local 与 SQL lowering 两条执行路径结果一致。
3. 实现两侧对齐表编码、`CorrespondencePart` 版本与 `graph_dag/v8`，按 5.2 改造各消费方。
4. 更新 period 相关测试的期望输出形态。
5. 更新 `timezone-and-calendar-design.md` 与站点文档。
6. 验收同 W1；period 比较涉及 Runtime 的部分用 `make runtime-test TESTS='...'` 跑受影响的文件。

W3：在 W2 合入后单独出实施计划。

## 11. 已决事项

- `relative` 分母保持 `abs(baseline)`，结果符号即变化方向（4.1）。
- 同端点 cohort 自比较拒绝（3.2）。
- 时间桶对齐由 analysis 比较契约唯一拥有，τ 族闭合为四个成员（8.2）。
- W2 紧随 W1 实施。
- 非 Defined 操作数按单元格传播，两侧都非 Defined 时优先级 Unknown > Undefined > Null，作为 W0 先于 W1 交付（5.3）。

## 12. 待决问题

- W2 的输出形态改变对下游消费方的实际影响，以 W2 第 1 步的清单为准；若有消费方必须保留 `anchor` 时间坐标，需在该步决定方案。
