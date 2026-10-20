# Predicate 统一设计：源端布尔条件与结果布尔 Column

Status: proposal，2026-10-20；仅设计，未实施，未完成独立审查或运行验收。

本提案以 `9f2d92508e1a569cb2de5c3504b3a75581e5f0e5` 的实现及当前拥有者合同为基线。
本文中的混合条件和目标签名不是当前已支持能力。本次只新增本文，不修改代码、
测试、当前规范、Help 或打包技能，也不代表授权实施。

拥有者合同：

- [Python Analysis Design](python-analysis-design.md)：来源构造、Population、Frame 及依赖边界。
- [Semantic Expressions](../semantic/expression-model-redesign.md)：原生 Deferred、
  来源规约、贡献根、条件需求域与检查。
- [AnalysisFrame](query-result-reuse.md)：精确输入绑定、固定数据与来源桥接。
- [Operators and Frames](operators-and-frames.md)：Null、完整键、数值政策及贡献次数。
- [Population 重构](population-refactor-design-and-plan.md)：坐标域、消费者分配规则和域计数。

## 1. 目标与取舍

目标是让字段条件和 Population 成员条件可以使用同一套布尔组合，并消除没有独立行为的
ColumnPredicate。保留来源贡献、已有坐标域和当前结果行三个消费位置的语义。

确定以下设计：

1. 源端谓词统一为受控原生 Deferred。`in_population(P)` 返回同一表达式家族中的
   成员判断叶子；可以与字段布尔表达式通过 `&`、`|`、`~` 组合。
2. 使用一个内部条件表示和一个接收/依赖收集流程，保留完整布尔结构。
   字段数值计算继续复用原生 Ibis recipe；不新增数值 AST。
3. Population.where 只接收由成员判断组成的条件。统一表示不授权它读取任意属性。
4. Frame.where 继续接收同输入的布尔 Column。删除 ColumnPredicate 空子类，
   不把 Column 转换成 Deferred，不恢复 Frame.where(PopulationPredicate)。
5. 删除公开 PopulationPredicate 类型及其专属 Help/show；不保留包装类型、别名、
   双入口或旧表达式读取分支。条件事实由接收者的有界 show 展示。

本次能力增量仅为现有源端消费位置中的字段/成员布尔组合。关系导航、时间角色、
分配规则、结果回源、来源规约方法及物理执行路线沿用拥有者合同。
不新增通用 Predicate 构造器、自动 join、跨阶段条件转换或独立执行器。

## 2. 当前实现与问题

| 当前形式 | 实际表示 | 差异及问题 |
| --- | --- | --- |
| 字段条件 | Deferred；`FieldPredicate` 是 `RowExpression` 的别名 | 已复用原生表达式，不是一套独立 Predicate 类 |
| Population 条件 | PopulationPredicate；PopulationContains/And/Or/Not | 自有布尔树和依赖载体，无法与字段条件自然混合 |
| 结果条件 | ColumnPredicate；ValueExpression | 子类没有额外字段或行为，Frame.where 实际按 Column 的 kind 和输入绑定检查 |

当前 `session.observe` 将 Population 条件包装进 ContributionFilter，将字段条件
作为 common condition 传入绑定器；ContributionBinding 再分开保存字段和成员条件。
Population.where 与事实贡献过滤也各有一份成员条件递归解释。
这种分流只能方便地处理合取，不能靠“先筛字段，再筛成员”表达混合 OR。

本轮分析的无业务读取构造探测确认：

```python
(status == "paid") & mv.in_population(P)
(status == "paid") | mv.in_population(P)
```

均在语义消费时被拒绝；反向 `mv.in_population(P) & (status == "paid")`
还会暴露普通 TypeError。该证据只说明当前接收缺口，不是目标实现验收。

实现定位：

- [字段表达式](../../../marivo/_definition/expressions.py)、
  [成员条件定义](../../../marivo/_definition/relations.py)。
- [Deferred 消费](../../../marivo/semantic/_deferred_semantics.py)、
  [observe 捕获](../../../marivo/analysis/dsl/source_capture.py)、
  [贡献绑定](../../../marivo/analysis/binding/observe.py)。
- [Population 公开表达](../../../marivo/analysis/dsl/population.py)、
  [Column](../../../marivo/analysis/dsl/column.py)、
  [Frame](../../../marivo/analysis/dsl/frame.py)。

## 3. 目标公开表达

### 3.1 字段与 Population 条件组合

以下假定 status、revenue 已解析，preferred_orders 是与订单贡献可绑定的 Population。
所有示例使用同一个 Session 的依赖。

```python
import marivo.analysis as mv

paid = status == "paid"
preferred = mv.in_population(preferred_orders)

condition = paid | preferred
result = session.observe({"revenue": revenue}, where=condition)

paid_preferred = revenue.where(paid & preferred)
other_orders = revenue.where(~(paid | preferred))
```

规则：

- 字段比较、isnull、isin 等已有合法布尔表达式保持原语法。
- `in_population(P)` 是一个不可变的布尔表达式叶子，构造时不枚举 P、不读取来源。
  P 可以是字面域、来源域或经显式 population_from 得到的 Frame 键域。
- `&`、`|` 的两侧可以是字段条件、成员条件或它们的组合；操作数顺序不改变准入规则。
  `~` 对整个子条件取反；括号明确布尔优先级。
- Python `and`、`or`、`not`、if 和 bool 转换均不能求值条件。
  不新增逐行 Python 回调、任意 Ibis 表输入或字符串表达式。
- 组合中的 True/False 常量沿用原生布尔表达式规则。顶层消费者不接受裸 Python bool；
  至少捕获一个合法字段或成员叶子。常量不得绕过其余叶子的静态检查。
- 含成员叶子的表达式首版只开放 `&`、`|`、`~` 和布尔常量组合。
  不因此开放成员叶子的算术、规约、比较、ifelse/cases/coalesce、cast 或任意方法。
  纯字段子表达式已有的原生能力保持不变；例如合法的纯字段 ifelse 可以作为字段条件叶子。
- source reduction 的 `where=` 保持现有“同行字段条件”合同，统一经过条件接收流程，
  但拒绝成员叶子。需要成员组合时使用规约后的 `Metric.where`。

同一来源规约上的 `metric.where(A).where(B)` 将 A、B 作为该使用位置的合取条件，
与 `metric.where(A & B)` 保持相同的选择结果和所需错误。
它不是按调用顺序逐步缩小条件检查域的短路管道。
`A | B` 必须保留为 OR，不能用连续 where 替代。

### 3.2 消费位置与目标签名

下列为目标签名片段；未列出的参数保持原合同。Deferred 指原生类型，
具体条件阶段和允许叶子由消费者校验，不另导出 Predicate 类型别名。

```python
from ibis.common.deferred import Deferred

def in_population(population: Population) -> Deferred: ...

def observe(
    self: Session,
    metrics: Mapping[str, Deferred],
    *,
    by: tuple[Deferred | Grouping | TimeScope, ...] | Population = (),
    where: Deferred | None = None,
    memberships: tuple[Grouping, ...] = (),
) -> AnalysisFrame: ...

def members(
    self: Session,
    entity: Entity,
    *,
    at: InstantAnchor | None = None,
    where: Deferred | None = None,
) -> Population: ...

def where(self: Population, predicate: Deferred) -> Population: ...
def where(self: AnalysisFrame, predicate: Column) -> AnalysisFrame: ...
```

Metric.where 仍是受控 Deferred 语法 `metric.where(predicate)`，
其参数为通过下表准入的源端条件；不增加包装 Metric 类。

| 消费位置 | 合法叶子 | 条件作用对象及限制 |
| --- | --- | --- |
| session.observe(where=...) | 字段、成员、两者混合 | 对每个事实规约使用位置分别绑定并过滤贡献；不改变显式输出域或重选 P.count 的 P |
| 单一来源 Metric.where | 字段、成员、两者混合 | 该来源规约或已有共同配对作用域；复合、采样后的 Metric 不因统一而取得 where 权限 |
| session.members(where=...) | 字段、成员、两者混合 | 显式 Entity 在 at 下的来源身份；字段根仍须满足该 Entity 的现有约束 |
| Population.where | 仅成员及其布尔组合 | 对接收者已有完整元组求值，每个成员域的轴都须可从接收者投影 |
| 来源规约的 where 参数 | 仅已有同行字段条件 | 保持原生规约的当前准入范围 |
| AnalysisFrame.where | 同一精确输入的布尔 Column | 当前结果行；不接收 Deferred、Population 或 Artifact |

Population.where 即使接收的字段看起来已在其 axes 中，也不开放字段表达式。
字段判断继续使用 members、显式 read 后的 Frame.where，或 session.frame(P) 的坐标 Column；
需要回到来源时再通过已有 population_from 显式桥接。
该选择保持 Population 的集合代数职责，避免统一过程中再引入一种属性解释方式。

### 3.3 ColumnPredicate 收敛

删除 ColumnPredicate 类和所有按布尔类型选择该子类的分支。
比较、`&`、`|`、`~`、is_null、布尔值读取及 presence 读取统一返回 Column，
其中 `_kind == "boolean"`；is_null 的公开返回注解同步为 Column。

保留以下行为：

- Column.__bool__ 拒绝 Python 真值求值。
- Frame.where 必须核对 Column 的精确 input identity 和布尔 kind；
  名称、schema、计算指纹或键集合相等不足以共享输入。
- select、where、top、compute 等操作之后，调用者按其返回 Frame 重新取得 Column。
  跨 Frame 组合仍需先 align；精确存储输入的兼容规则沿用既有合同。
- 布尔 Column 可由比较产生，也可以直接读取布尔值或 presence。
  where 只保留 True，False/NA 不选中；保持相对行序、轴、presence 和冻结时间目录。
- 值 NA 与 presence=False 仍是不同事实，不能相互推导。
- 不要求 Frame 的 ValueExpression 与源端 Ibis Resolver 共用表示或执行内核。
  来源/当前数据是逻辑阶段划分，不等同于远程/本地的物理位置划分。

## 4. 布尔、成员和错误语义

### 4.1 三值布尔与非 nullable 成员叶子

字段条件沿用 nullable boolean：True、False、NA。
成员判断为非 nullable boolean：命中 True，未命中 False。
二者组合使用已有三值布尔规则；只在最终筛选边界选择 True。
禁止在子条件组合之前将字段 NA 填为 False。

| 字段 F | 成员 M | F & M | F \| M | ~(F \| M) |
| --- | --- | --- | --- | --- |
| True | True | True | True | False |
| True | False | False | True | False |
| False | True | False | True | False |
| False | False | False | False | True |
| NA | True | NA | True | False |
| NA | False | False | NA | NA |

特别地，`~(F | M)` 不能先把 F 的 NA 当作 False，否则会错误选中未知值行。
`in_population(P)` 的 Null 匹配也不能改成原生 SQL NOT IN 的空值语义。

### 4.2 完整坐标与集合成员

对接收者的一行 r，成员条件的含义为：

```text
contains(P, r) = project(r, P.axes) is a complete tuple in P
```

- 多轴键保持相关性；不能把一组完整元组拆成独立列的 isin 再取 AND。
- 复合身份保留全部主键分量、声明顺序及所需业务版本锚点。
  相同标量值但不同 Entity、角色或属性时点不能自动视作同一坐标。
- 字段 Null 是合法类别，成员匹配使用现有 null-safe 坐标等值；
  身份键仍禁止 Null，不能借统一改变身份有效性。
- 空 P 的成员判断恒为 False；零轴单元域 `{()}` 对每个已有接收者行恒为 True。
  二者仍是不同的域。接收者为空时，取反也不会生成行。
- `~in_population(P)` 是相对于消费域的补集，不构造全局 Entity 宇宙。
- 成员判断只给每条贡献/坐标一个布尔值，不能放大行数或复制贡献；
  域键重复不增加权重，等值但不同身份的事实仍独立计数。
- in_population 与 `membership(..., paths=...)` 的分配语义保持独立。
  条件选择不能改变既有分配分母、重复链接去重或 unmatched 规则。

### 4.3 检查需求域

沿用 Semantic Expressions 的条件需求域，不新增短路语义：

1. 所有叶子都进行静态闭包检查，包括常量决定结果的分支。
   根、单位、类型、方法准入、Session、坐标和依赖绑定错误不能被 True/False 遮蔽。
2. 在一个贡献使用位置的条件输入域 D 上，`&`、`|` 的两个操作数都需要求值和检查。
   `M & risky_field_test` 不能因 M=False 而吞掉该域内字段条件的严格除零等错误。
3. 纯字段叶子内部的 ifelse/cases/coalesce 继续遵守它们已有的需求域。
   不因外部统一而改成 eager 全分支计算，也不因外部布尔条件获得额外短路保护。
4. 条件得到最终掩码后，来源规约的数值、严格 NA 等检查在已选贡献上进行；
   后续 Frame.where/select/top 不反向缩小已经需要执行的检查域。
5. Population 生产者自身的既有检查不能被成员条件、空键域或布尔化简删除。
   不承诺未选分支完全不访问来源，不把连接、解码或读取失败转换为 False。

当前 session.members 的执行路径会拒绝需要严格数值检查的字段谓词；
本提案保留该限制，要求混合表达式也在准备阶段一致拒绝，不能绕过或丢弃检查。
本次不顺带扩展该消费者的数值执行能力。

## 5. 内部表示与执行路径

### 5.1 一种条件表示，两个封闭叶子种类

目标采用一个私有 PredicateExpression 记录，放在 definition 层的条件所属模块。
名称为实施定位，不构成公共导出：

```text
PredicateExpression
  recipe: native Ibis Resolver over boolean input slots
  inputs: ordered tuple[RowExpression | PopulationContains, ...]

RowExpression
  existing native field/numeric recipe and semantic field inputs

PopulationContains
  one inert PopulationDependency
```

条件的布尔连接由原生 Resolver 表达。纯字段布尔子树可整体作为 RowExpression 输入；
成员叶子由 PopulationContains 表达。收集器为叶子分配稳定槽位，保持操作结构，
不再同时维护 PopulationAnd/Or/Not 或另一套通用 And/Or/Not。
纯字段条件、纯成员条件及混合条件均规范化为这个记录。

`in_population(P)` 通过一个受控成员 Resolver 捕获 P，方式与已有特殊 Deferred
捕获机制一致。禁止调用用户回调或通用 Deferred.resolve 来猜测成员含义。
原始捕获可以强引用 P 的调用期载体；规范化后的定义只保存惰性定义和显式键引用，
不保存 Session、Catalog、AnalysisFrame、运行时连接或业务行。

FieldPredicate 角色别名可随消费者迁移移除，统一由 RowExpression 表示纯字段叶子、
PredicateExpression 表示完整条件。PredicateFacts 若继续使用，只是静态事实结果，
不新增第四种可组合谓词。

### 5.2 接收、绑定与依赖闭包

共享接收流程的输出包含：规范化条件、字段语义事实、Population 依赖，以及
独立保留的调用期 FrameKeyInput。各消费者在其上施加第 3.2 节的限制。

必须先从原生 recipe 收集强引用输入，再丢弃公开载体：

```text
native Deferred recipe
  -> collect complete dependencies and strong Frame inputs
  -> validate allowed predicate grammar and leaf kinds
  -> normalize to inert PredicateExpression
  -> validate the consumer's domain and bind every leaf
  -> prepare membership markers and field expressions
  -> evaluate the original boolean recipe and its checks
  -> filter once at this contribution/domain boundary
```

绑定规则：

- 字段子树保持现有根/单位/类型规则。把不同字段根分放在成员叶子两侧，
  不能绕过原本的同根要求；显式 via 仍按既有单值路径规则建立根。
- observe 的通用条件按每个事实规约 occurrence 分别重绑定。
  每个字段和成员轴都必须能在该 occurrence 上合法解释；OR 中不可绑定的分支也拒绝。
  不通过跨事实根 join 或把某一分支当 False 完成绑定。
- Metric.where 保持单一来源作用域；session.members 保持显式 Entity 的身份作用域。
- Population.where 只检查接收者的现有坐标投影，不读取额外字段。
- 纯字面 Population 可无 Session owner；一旦条件包含有主依赖，
  其 Store/Session 必须与消费者及其它有主依赖一致。
- FrameKeyInput 的 input identity、完整存储描述和强引用按当前规则合并。
  多次引用同一合法输入可以共享调度，不能只按计算指纹合并独立数据输入。
- 不要求原始定义版本相同；保持当前同路径重解析政策以及 kind/root/grain/unit/
  time/null 校验。计算指纹描述实际消耗的定义，审计记录保留自身合同。

### 5.3 保留混合 OR 的编译方式

先在当前贡献关系上取得所有合法字段值，并为各 Population 依赖准备布尔成员标记。
成员标记使用现有完整键传输、null-safe 匹配和无 fanout 的存在性判断。
之后把字段谓词值和成员标记绑定到 PredicateExpression 的槽位，
按原始 Resolver 计算一个最终布尔掩码。

`F | M` 的准备阶段不能先用 M 做 semi-join 或先 filter(F)，否则另一分支可选中的行已丢失。
即使 F & M 的最终选集允许交集优化，也必须先证明原条件检查域及生产者检查被保留。
字段检查所需关系不能被成员预过滤悄悄缩小。

ContributionFilter、SourceIdentities、PopulationFilter 和观察绑定统一携带条件表示。
ContributionBinding 可以保留“内在条件”和“本次公共条件”的来源归属，
但二者均使用同一表示；移除依据字段/Population 类型拆分的语义存储与执行分支。
字段读取表和成员键绑定表可以继续作为编译所需事实存在，不成为另一套条件权威。

来源贡献与 Population 坐标选择复用条件结构解释和成员标记规则；
底层 Ibis 表达式与已有本地集合执行仍按各自数据载体实现。
不为减少几个分支而引入通用执行后端接口。
本轮不扩大固定 Frame 键上传、跨源融合或 provider 支持范围；
已有合法传输路线继续使用，不可执行的路线必须保留结构化拒绝。

### 5.4 指纹、检查与持久化

指纹纳入完整布尔 recipe、稳定输入顺序、成员域及其坐标绑定、实际字段定义和相关政策。
同一捕获表达式提取到变量不会改变其意义或依赖。
本轮不要求交换律等价表达式具有相同指纹，也不增加代数规范化或恒等式消除。
即便选择结果恒定，优化仍须保留按合同需要执行的生产者和检查。

调用期 Frame 强引用不进入持久化语义定义。来源事实以现有审计机制保留有界、惰性描述，
不保存可恢复执行的 Predicate 或生产者图。
已完成/已存储 Frame 的读取仍不依赖重新加载来源模型。
本次不改变 Frame 数据格式或承诺旧表达式指纹不变；
若实施发现必须改变存储字段合同，必须回到存储拥有者设计，不能私自加入双读。

## 6. 必须保持的分析边界

| 边界 | 本提案的确定规则 |
| --- | --- |
| 聚合前后 | 来源 amount > 100 与结果 revenue > 100 不互换；两笔 60 的订单可得到不同选集 |
| 输出域 | by=P 保留 P 的完整键，包括无事实坐标；where 过滤贡献，不把这些键删掉 |
| 空值与空域 | 默认 sum 的空贡献为 NA、显式 empty="zero" 为 0，count 为 0；空输出域仍无行 |
| Population.count | 只数捕获的完整元组；混合条件不重选其 P；纯 count 请求的无事实条件仍拒绝 |
| 多事实根 | 各 occurrence 独立绑定、过滤和规约，再按完整输出键组合，不能互相乘行 |
| 配对与采样 | weighted_mean 的值/权重共用同一已选贡献；per 的条件不能跨过样本规约移动 |
| 时间 | 字段 within 等硬条件不提供窗口端点；by 的时间权威、窗口及属性 at 均保留 |
| 日历成员 | 来源成员条件仍只接现有可绑定普通字段/身份轴；不新增 interval/calendar 回源或非平凡日历子集入口 |
| 固定选择 | 固定 Frame 桥接的键不随新来源重排/替换；pending Frame 仍按使用时求值；新观测不承诺与选择共享来源快照 |
| 生命周期 | 构造、接收和 show 不读取业务行、不隐式 compute/persist 或创建 Run；执行使用现有单次调度 |

这些规则也约束优化。不能把“统一 Predicate”解释成允许过滤位置随意交换。

## 7. 错误、Help 与删除范围

混合条件的静态准入错误在消费者捕获阶段报告；真实 dtype、路径物理能力等
需要准备信息的问题在准备阶段报告；数据数值错误按已有执行边界报告。
所有错误使用所属 SemanticError/AnalysisError 和共享模板，包含 expected、received、
实际失败叶子/位置和具体 repair。原生 Deferred 的 Python 真值拒绝沿用原生机制。

必须覆盖这些 repair：

- Population.where 收到字段叶子：说明它只筛现有成员坐标，指向 members 或显式 Frame 筛选。
- Frame.where 收到源端 Deferred：说明需要该 Frame 的布尔 Column。
- 字段/成员无法绑定某事实根：指出具体叶子和目标根，要求显式合法路径或改为相应子 Metric 的条件。
- 外部 Session 的 Population/Frame 依赖：指出归属冲突，要求在拥有该输入的 Session 构造。
- 成员条件用了不支持的方法：说明支持的布尔组合，不能泄漏 AttributeError/TypeError。

实施时同步以下公开面：

| 公开面 | 目标 |
| --- | --- |
| analysis.in_population | 唯一成员谓词生产入口，返回 Deferred；说明完整元组与 Null 匹配 |
| semantic.predicate | 新的语义主题页，统一拥有源端布尔语法、组合、阶段和消费者限制；不导出 Predicate 类型 |
| semantic.field / semantic.metric.where | 链接统一语法，保留字段及来源规约自身合同 |
| analysis.session.observe / members / population.where | 更新目标签名和各自准入限制、失败修复及最小例子 |
| analysis.column / analysis.analysis_frame.where | 返回及接收普通布尔 Column，保留精确输入与 NA 规则 |
| Population/Frame 的 show | 有界展示已捕获条件结构和依赖；不得 resolve recipe、枚举域或触发 getter/read |
| 导出与旧页 | 从 __all__ 删除 PopulationPredicate；删除专属类型/show 页及导航，不保留别名；ColumnPredicate 原本不公开导出 |

同步 source facts、依赖遍历、native Help registry、reachability/budget 测试、API 文档、
当前规范和 `site/src/content/docs/*/latest/` 中受影响的中英文示例。
静态语法由 Help 拥有；实例事实由消费者 show 拥有，避免再复制一个表达式能力清单。
不为 Deferred 添加 show 方法，也不引入独立条件检查对象。

此次读取未在两个打包技能的 SKILL.md 中发现上述类型或构造器名；
实施时仍须核对其工作流是否受影响。本提案不授权修改 AGENTS.md 或打包技能；
若需要技能修改，应单独列出具体对齐内容并取得明确授权，不以此阻塞当前设计交付。

## 8. 验收标准

以下为后续实施的必要验证，均为待执行；不展开 provider × 类型 × 载体的全排列。
优先复用现有 fixtures。对公开路径使用独立字面结果或明确 SQL/元组集合 oracle，
不能只验证新内部类型或比较两条共享同一错误实现的路径。

### 8.1 核心数值见证

订单数据固定为以下六行，身份轴为完整 order_id，P 为 O1/O2/O3：

| order_id | status | amount |
| --- | --- | --- |
| O1 | paid | 60 |
| O2 | pending | 40 |
| O3 | Null | 10 |
| O4 | paid | 20 |
| O5 | cancelled | 30 |
| O6 | Null | 5 |

令 F 为 `status == "paid"`，M 为 `in_population(P)`：

| 条件 | 应选身份 | amount.sum |
| --- | --- | --- |
| F & M | O1 | 60 |
| F \| M | O1、O2、O3、O4 | 130 |
| ~(F \| M) | O5 | 30 |
| F & ~M | O4 | 20 |

这些见证分别通过 observe、单一来源 Metric.where 和 members 实际执行。
正反操作数顺序和嵌套组合必须符合相同真值规则；成员 only 与字段 only 的原有行为保持。
Population.where 另以纯成员条件验证集合选择，并验证混入字段条件的结构化拒绝。

### 8.2 验收清单

| ID | 验证内容 | 通过标准 |
| --- | --- | --- |
| U01 | 组合与消费者准入 | 8.1 的选集/金额准确；两种操作数顺序、括号嵌套和消费者矩阵一致；非法成员方法与跨阶段输入有结构化 repair |
| U02 | 三值与完整元组 | 第 4.1 节六种结果成立；联合域 `{("east", "web"), ("west", None)}` 不误选 `("east", None)` 或 `("west", "web")`；复合身份不串租户，字段 Null 匹配而身份 Null 拒绝 |
| U03 | 空域与相对补集 | 空 P、零轴单元域和空接收者行为准确；取反不造行；空贡献默认 sum=NA、显式 zero=0、count=0；by 中无事实坐标保留 |
| U04 | 检查域与生产者错误 | 布尔恒定分支仍拒绝非法根/单位/类型；M=False 行上的严格字段条件错误不被 AND 吞掉，M=True 也不能让 OR 吞掉另一叶子错误；纯字段 ifelse 的未选数值分支仍按原合同保护；members 的严格检查限制不能被混合绕过 |
| U05 | 根、配对、时间及计数 | 多事实根分别过滤而不乘行，不可绑定的 OR 分支拒绝；共同配对和分配次数不变；硬时间条件不代替窗口端点/属性 at；P.count 不重选，count-only where 拒绝 |
| U06 | 依赖与固定键 | 深层 &/OR/NOT 和嵌入 Metric 都保留 FrameKeyInput；同 Session 强引用、完整描述合并及跨 Session 拒绝正确；含失败生产者的恒定成员表达式不吞错误；来源变化后固定键不变，pending 键按使用时求值 |
| U07 | Column 收敛 | 比较、取反、is_null、直接布尔值和 presence 均为普通 Column；where 仅选 True；错误 Frame 的 Column 拒绝；移除子类前后值/轴/presence/顺序和存储后数据操作一致 |
| U08 | 生命周期与披露 | 构造/show 不读取 schema 或业务行、无隐式 compute/Run；新 Help 和导出快照一致，旧类型/页面/分支退出；中英文例子与真实执行一致 |

U04 的严格数值见证可在上述 O3 上增加零分母，再用不含 O3 的成员域形成 AND，
以及包含 O3 的成员域形成 OR：条件自身的严格除法都不能被另一布尔叶子保护。
相同除法若位于合法纯字段 ifelse 的未选分支，则继续遵守已有需求域规则。

U05 需用独立计数见证区分：
字面身份域 `{O1, O2, O3, O999}` 的 P.count=4，而来源在该域内的身份 count=3。
同一个混合公式中附加事实条件只能改变事实项，不能把分母 4 改成 3 或重新选域。

U06 需通过公开 population_from 构造依赖，不能只手造旧 Query 节点；
使用已经 compute/persist 的输入固定键，并保留另一个 pending 输入作为对照。
必要的检查不能以递归执行生产者、关闭失败记录或重放旧来源来完成。

### 8.3 验证层级和完成条件

实施从受影响的条件构造、Population、观察绑定、数值筛选和 Frame 测试开始。
修改的 Python 模块必须通过对应 typing/lint；共享条件行为变化后完成不缩小范围的
`make check-agent`，并验证受影响 API/site 内容、链接与中英文示例。

本地 DuckDB 的公开执行见证建立基础正确性；已有支持的远端若改变 lowering，
运行相关 Runtime 条件见证并单独记录 provider/type/route。
没有远端证据不能宣称该路线已获新的资格。普通设计或实施收尾不运行 release-check、
不启动无关基础服务，也不以本地 gate 代替 release 验收。

实施完成要求 U01–U08 有明确证据，删除范围和公开面一致，没有未解决的阻塞问题。
本提案的文档校验不满足任何运行验收项。

## 9. 后续实施顺序与本轮证据

以下是责任与依赖划分，不是本轮已开展的开发或多 agent 授权。
实施前另建运行进度记录，保存失败、修复、审查和最终冻结源码的证据。

| 阶段 | 责任范围 | 依赖 | 可核验交付 | 状态 |
| --- | --- | --- | --- | --- |
| S1 | definition / semantic / dependency capture | 本提案确认 | 原生成员叶子、统一条件接收与惰性/强引用分离；U01/U06 构造检查 | 未开始 |
| S2 | binding / compiler / materialization | S1 | 全部消费者迁移，完整布尔求值及检查域；U01–U06 执行见证 | 未开始 |
| S3 | Column / Frame | 无需改变来源语义 | 移除空子类，统一布尔 Column；U07 | 未开始 |
| S4 | Help / docs / tests / cleanup | S1–S3 | 同步公开面，移除旧表示/分支，完成 U08 和最终 gate | 未开始 |

各阶段更新直接相关的测试和披露，S4 核对最终完整状态。
最终源码冻结后再作统一验收；不把中间新旧入口共存当作交付。
若另行安排正式独立审查，遵守最多两轮、未关闭 P2+ 阻塞收尾的约束，
并将审查与运行证据分别记录。

本轮仅有当前合同/代码/Live Help 的只读核对和前述构造探测。
文档交付检查已通过：13 个相对文件链接、3 段 Python 示例语法、
8 个唯一验收 ID、代码围栏和新增文档的空白差异检查。
按独立字面数据复算了第 8.1 节四组选集/金额和第 4.1 节六行真值表，均一致。
工作树复核确认本轮只新增本文；其余已有改动未纳入本轮编辑。
这些结果只验证设计文档，没有实施、业务执行、独立审查、Runtime 或 release 结果。
