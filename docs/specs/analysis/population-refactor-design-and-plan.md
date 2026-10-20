# Population 重构设计与实施计划

Status: implemented / locally validated，2026-10-20。用户已授权按历史 agent 团队模式实施；
实际进度和检查见 [实施记录](population-refactor-implementation-progress.md)。

核对基线：`da81a5a447c9df2cc20ee20cf2ba80bff9ecf261`。当前行为以
[Python Analysis Design](python-analysis-design.md)、
[AnalysisFrame](query-result-reuse.md) 和 Live Help 为准。
本文将 [Session/observe 设计](session-observe-redesign.md) 中的身份域计数进一步泛化，
并调整 membership 的参数归属；以下接口已实施，最终检查记录与历史基线证据分开保存。

起草轮仅新增本文和架构索引链接；本轮实施包括代码、测试及当前合同同步。
AGENTS.md 和 packaged skills 不在授权编辑范围，不提交、不推送，保留无关工作树改动。

## 1. 目标、取舍与范围

Population 保留为一个公开类型：

> 带有明确语义坐标、所有权和求值依赖的有限去重键域。
> 定义可以尚未求值；Population 不包含指标值，也不决定事实如何分配到桶。

本次做四件事：

1. 统一字段、身份和相关多轴字面键的构造，删除 `mv.identities`。
2. 将 membership 规则从 Population 移到 observe / Metric.per 的消费参数。
3. 将 `Population.count()` 定义为完整元组计数，新属性通过显式 classify 获得。
4. 修复已确认的输入错误和披露缺口，同步实际受影响的公开面。

保留 project、classify、集合运算、where、image/preimage，以及 Frame 桥接；
它们分别表达投影、属性读取、集合代数、成员资格和关系遍历，不为减少方法数量强行合并。
不拆分 IdentityPopulation / CoordinatePopulation，不引入通用 join、独立执行器、
新 Store、自动映射、兼容别名或双重参数解释。

代价是身份字面键多一层元组，以及 membership 在消费端需要明确声明。
换取的是一个完整的字面构造合同、纯键域代数和可独立复用的贡献映射。
本次不顺带开放新的关系基数、CalendarAxis 桥接、配对轴桥接或 Metric.per 样本类型。

## 2. 必须保持的语义

| 概念 | 合同 |
| --- | --- |
| 完整联合键 | 去重、投影、过滤和集合运算按完整元组处理，不把相关元组拆成独立键后相乘 |
| 坐标顺序 | 轴有确定顺序；集合兼容要求相同有序业务坐标，不自动重排 |
| 字段与身份 | 字段 Null 是有效分类；身份包含全部非空主键分量，普通字段不会因值像主键而成为身份 |
| 版本 | 身份/字段锚点保留原义；同路径定义版本变化本身不构成拒绝理由，结构和时间语义仍须相容 |
| 来源存在性 | 字面身份只声明引用；members 选择实际来源身份；读取属性需要对应记录，image 保留可由完整外键确定的缺失目标引用 |
| 域与贡献 | `by=P` 选择完整输出键并保留无事实坐标；`where=mv.in_population(P)` 筛选事实贡献 |
| 惰性与固定 | 构造不读业务行或 schema；pending 选择依求值重新执行，固定 Frame 桥接保持已选键 |
| 空与零轴 | 空域为 `∅`；非空域投影到零轴为 `{()}`；空域投影到零轴仍为空 |
| 所有权 | 字面域可以未绑定 Session；有 Session 的依赖必须属于同一接收 Session |
| 求值 | 复用现有 compiler/scheduler；依赖检查不能因投影、空输出或后续筛选而被删除 |

Population 的行次序不新增数学含义，继续使用现有确定性输出规则。
不承诺同一惰性定义两次求值具有相同来源快照。

## 3. 目标公开接口

### 3.1 构造与桥接

以下是本次实现签名。`CoordinateValue` 表示现有内部键值联合：
`LiteralValue | tuple[LiteralValue, ...]`；这里只说明类型，不新增顶层导出。

```python
def coordinates(
    *,
    by: tuple[Deferred | Grouping, ...],
    keys: tuple[tuple[CoordinateValue, ...], ...],
) -> Population: ...

def product(*factors: Population | CalendarAxis | TimeScope) -> Population: ...

class Session:
    def members(
        self,
        entity: Entity,
        *,
        at: InstantAnchor | None = None,
        where: Deferred | None = None,
    ) -> Population: ...

    def population_from(
        self,
        frame: AnalysisFrame,
        *,
        on: tuple[tuple[FrameAxis, Deferred | Entity], ...],
    ) -> Population: ...
```

`coordinates.by` 接受直接或已有受治理绑定的字段坐标，以及完整 identity Grouping；
拒绝 membership、interval/calendar 和 FrameAxis。identity Grouping 使用 Entity 形式的
`mv.identity(entity, at=...)`，不接受携带关系路径的 identity Grouping：
字面目标身份不需要另一贡献根到它的分组路径。
单个身份轴的一个值永远是完整主键元组，不能按主键宽度改变输入形状。

```python
# Unified literal coordinate examples.
customers = mv.coordinates(
    by=(mv.identity(Customer),),
    keys=((("C1",),), (("C2",),), (("missing",),)),
)
versioned = mv.coordinates(
    by=(mv.identity(Customer, at=instant),),
    keys=((("C1",),),),
)
joint = mv.coordinates(
    by=(mv.identity(Customer), region),
    keys=((("C1",), "east"), (("C2",), "west")),
)
unit = mv.coordinates(by=(), keys=((),))
empty = mv.coordinates(by=(), keys=())
```

字面元组声明求值坐标，不证明来源中该身份具有这些属性。混合域中的字段值与来源不一致，
按当前显式输出域规则得到相应无贡献单元，不通过隐藏属性读取“纠正”字面输入。
构造时校验行宽、同一身份轴各行的主键宽度一致、身份 Null 和重复轴。
声明主键宽度若已在捕获元数据中可用则同时校验，否则留在现有准备阶段，
不为此增加一套元数据缓存；真实物理类型检查也不提前打开来源。

`mv.identities` 从导出、Help、示例和实现删除，不留别名。
对应迁移是把每个主键元组包成一个轴值，并把 Entity 改为 `mv.identity(entity)`。

`product` 保留至少两个因子、轴不重复和最多一个输出时间轴的现有限制。
TimeScope / CalendarAxis 因子使用其冻结的完整有限域；不允许手写日历 ordinal 伪造键。
单一时间分组继续直接用于 observe.by；不为单因子转换再增加工厂。

members 和 population_from 不合并进 coordinates：前者声明来源枚举，后者显式绑定既有
Frame 键，二者都提供字面值之外的独立输入。population_from 的 `on` 仍必填，支持 `on=()`；
普通字段与完整身份轴资格、联合投影、固定/pending 行为保持不变。

Population 继续导出供类型注解和返回值使用；成员判断返回原生 Deferred，不另导出谓词类型；
原始 `_definition` / `_condition` / `_frame_inputs` 不是公开构造合同。
不为误用内部构造器增加令牌、反射拦截、公开子类或防篡改机制。

### 3.2 Population 实例面

| 接口 | 目标决定 |
| --- | --- |
| `axes` | 保留有序单坐标 Grouping；不含选中键或 membership 规则 |
| `memberships` | 删除公开属性及 Help 目标；Population 内部也不再携带分配描述 |
| `project(*, by)` | 保留，只选取/重排已有坐标并去重；合法零轴投影 |
| `classify(*, by)` | 保留，单身份域读取直接同实体字段，可在输出中保留接收者身份 |
| `union(other)` | 相同有序坐标域求并，不再比较分配规则 |
| `intersection(other)` | 相同有序坐标域求交 |
| `difference(other)` | 相同有序坐标域求差 |
| `where(predicate)` | 保留，只接受原生 Deferred 中闭合成员叶子的布尔组合，允许用部分坐标域筛选联合元组 |
| `image(*, via, at=None)` | 保留有向关系映射；目标由路径推导，at 锚定目标身份 |
| `preimage(targets, *, via)` | 保留接收者范围内的存在选择；目标锚点来自 targets |
| `count()` | 泛化为第 5 节的完整元组计数 Metric |
| `show()` | 保留无来源读取的有界定义披露 |

`project.by` 和 `classify.by` 不再接受 membership Grouping。classify 的输出只能是直接字段
坐标或接收者原有身份；interval/calendar、routed field 和不同身份绑定在构造时拒绝。
本次不因为底层读取器有更多能力就扩大 classify 的路径范围。

当 classify 没有新增属性、只选择已有身份或零轴时，直接采用 project 语义：
`P.classify(by=P.axes)` 不新增来源存在性检查。真正读取新属性时继续要求每个所选身份有
唯一属性记录；缺失记录与属性值为 Null 是两种情况。

image/preimage 的公开 `via` 类型使用现有 `tuple[Relationship, ...]`，
不把内部 `RelationshipParameter` 当成用户构造对象。路径非空、有向且连接；
需要中间版本实体锚点时使用已有分步 image，不新增路径配置对象。

`where` 通过统一条件接收流程校验成员叶子与现有坐标，错误输入返回已有 AnalysisError 模板。
不把字段 Deferred 静默转换为来源读取；字段筛选使用 `session.members(..., where=...)`，或 read 后的
Frame.where。`P.where(in_population(Q))` 不能一律换成 intersection，因为 Q 可以只有 P 的部分轴。

集合兼容使用现有语义绑定规则。不能只比较旧 ExactRef 指纹来判断业务坐标不同，
也不能为了消除版本差异而丢掉 Field/Entity 种类、路径或业务锚点。

### 3.3 谓词与检查

`mv.in_population(P)` 返回原生 Deferred，支持 `&`、`|`、`~`；来源消费者可与字段条件组合，
不增加 equals、contains、is_empty 等会隐含求值的新方法。
原生 Deferred 不提供条件 show；接收者 Population.show 或 AnalysisFrame.show
展示有界捕获结构及域摘要，均打印并返回 None。Population 的 repr 保持单行并指向 show；
源端布尔语法与消费者限制由 `marivo.help("semantic.predicate")` 拥有。

Population 的 Python truth evaluation 明确拒绝，不把 `if P` 解释为空域检测。
`==` 不增加集合等价求解器；现有定义结构比较不作为公开集合相等合同。
实例 show、Help 和结构化错误承担披露，不再暴露私有 IR 来补充说明。

## 4. membership 只在消费端声明

### 4.1 唯一归属与参数

保留现有 `mv.membership(by, *, via, unmatched) -> Grouping`。
该值描述相关分类字段与贡献映射，不包含候选键。
使用两个消费端的同名关键字 `memberships`：

```python
class Session:
    def observe(
        self,
        metrics: Mapping[str, Deferred],
        *,
        by: tuple[Deferred | Grouping | TimeScope, ...] | Population = (),
        where: Deferred | None = None,
        memberships: tuple[Grouping, ...] = (),
    ) -> AnalysisFrame: ...

# Native Deferred grammar, not a new Python Metric class:
# metric.per(samples, memberships=(membership_rule, ...)).mean()
```

`memberships` 只接受 `kind="membership"` 的现有闭合 Grouping，不增加公开分配类型。
该参数是真正独立的输入：by/samples 决定坐标或候选域，memberships 决定事实到这些坐标的映射。
规则引用坐标以指定作用位置，不重新定义输出轴、选择键或补充默认域。

membership Grouping 不再放入 observe.by、Metric.per 的 samples、coordinates 或 classify；
同一能力只保留消费端参数这一种表达方式，不增加 `domain` / `using` / `allocation` 同义入口。
`mv.identity` 及普通字段、interval/calendar Grouping 的原分组用法不变。

```python
# Consumer-owned membership examples.
rule = mv.membership((tag, channel), via=(order_to_tags,), unmatched="exclude")
domain = mv.coordinates(
    by=(tag, channel),
    keys=(("A", "web"), ("B", "store")),
)
observed = session.observe(
    {"revenue": revenue},
    by=domain,
    memberships=(rule,),
)
sampled = session.observe({
    "mean": revenue.per(domain, memberships=(rule,)).mean(),
})
```

不提供显式 Population 时，使用 `by=(tag, channel), memberships=(rule,)` 发现实际联合桶。
提供 Population 时，只在其完整候选键上求值，并保留无事实键。

### 4.2 绑定规则

- observe 的规则只解释输出坐标；per 的规则只解释该次内部样本坐标，
  不隐式继承或覆盖另一层的规则。继续使用现有每个 contribution occurrence 的绑定路径。
- 每个规则的字段必须存在于对应 by/samples 的坐标中，保持捕获的字段顺序与绑定。
  同一层内一个坐标不能由两个不同规则重复分配；不自动选路径或合并规则。
- 一个多字段规则保留相关元组；两个不重叠规则保留当前独立分配语义。
  按 `(完整贡献键, 完整桶键)` 去重，不能按数值去重。
- `via` 继续是每个贡献根最多一条明确路径；没有所需路径时按现有规则报错。
  `unmatched` 仍必填为 exclude/null，后者表示来源未匹配桶，不负责扩张指定 Population。
- Population count 不读取事实，也不应用 membership 路径。规则必须实际作用于某个事实
  contribution，不能用 count-only 请求假装已经执行关系分配。
- in_population 仍是成员资格条件，不因删除 Population 分配字段而新增多值关系导航。
  需要存在性选择时继续使用显式 preimage。

Population IR 不再保存 memberships；观察和内部样本的定义捕获公开规则降级后的
MembershipBinding。定义指纹包含实际消费的规则；来源依赖闭包与现有去重/空值政策保留。
这是拥有者迁移，不重写关系执行器或引入另一套 fanout 算法。
per 必须能同时捕获原有 Population 样本依赖和本次 memberships；在现有样本定义内
调整字段即可，不通过复制 Population 或新增公开包装对象重新把二者绑在一起。

## 5. count：完整域元组的基数

`P.count()` 返回单位为 `1` 的整数 Metric，捕获 P 的依赖，不立即读取来源。
无分组时等于 P 的完整去重元组数；空域为 0，零轴单元域为 1。
字段 Null 元组参与计数；完整身份和多个身份轴都按一个完整元组计数。

分组时，普通输出/内部样本坐标必须已经存在于 P：
对当前单元匹配的 P 元组计数，不按投影后的组键先去重再计数，也不隐式查属性。
显式输出域中的缺组计数为 0；没有显式输出域时，候选组从 P 的投影与其他指标的现有
输出域组合规则获得。P/Q 分别按各自域计数，不互相缩减。
显式输出域为空时仍没有输出行；这与标量 `empty_population.count()` 返回 0 不矛盾。

```python
# Classify is the explicit source attribute dependency.
classified = customers.classify(by=(customers.axes[0], region))
counts = session.observe({"customers": classified.count()}, by=(region,))

# Region tuples and customer tuples intentionally count different things.
region_count = classified.project(by=(region,)).count()
customer_count = customers.count()
```

若只 classify 到 region，再 count，计数对象就是去重地区，不是原客户。
字面身份可在无属性分组时计入缺失身份；按新属性分组必须先读取或显式声明相应坐标，
不能为缺失身份推测一个 Null 分类。

时间规则保持单一解释：

- P 没有时间轴时，已有输出 interval/calendar 可以广播本次求值得到的同一选择域计数。
  这表示“每个时间格重复该域计数”，不是按时间重选成员。
- P 含时间轴时，按其完整冻结时间坐标匹配或在标量计数中计入全部元组；
  不能用另一不兼容时间轴重解释它。
- count 不获得事实时间角色；事实 where/window 不重新选择 P。
  count-only 条件/window 无事实作用时沿用现有拒绝行为。
- 不因 count 泛化扩大 Metric.per 已支持的样本种类或窗口能力；
  合法内部样本依然先完成整个子公式，再按原政策规约。

实现上删除 MemberCount 绑定时隐式创建 PopulationClassify 的分支，
以完整 Population axes/key groups 驱动已有计数编译与本地规约。
需要属性的调用点改为显式 classify；不增加公共 count 策略参数或第二个计数入口。

## 6. 实施顺序与责任

按用户后续授权采用历史 agent 团队模式：core 负责生产实现，tests 负责测试迁移与回归，
docs 负责当前合同及 latest 英中文档，coordinator 负责集成与台账；完成开发后独立审查及验收。
阶段名是工作划分，不是新增运行时状态；最多两轮正式审查，未解决 P2+ 阻塞收尾。

| ID | 责任 | 依赖 | 最小交付 | 状态 |
| --- | --- | --- | --- | --- |
| P1 | core / tests | 本文 | coordinates 支持身份/相关元组；删除 identities；修复 where/classify/show；迁移相关调用 | 完成 |
| P2 | core / tests | P1 | 两个消费端 memberships 参数；移除 Population 分配记录和公开属性；迁移原 membership 用法 | 完成 |
| P3 | core / tests | P1、P2 | 全元组 count、显式 classify 调用迁移、既有时间广播与条件语义 | 完成 |
| P4 | docs / coordinator | P1–P3 | Help/导出/文档一致，受影响检查与最终 broad gate 通过 | 完成 |

各阶段同步其修改到直接测试与 Help，P4 再做整体核对，不把中间不一致状态当成可交付版本。
默认不提交、不推送；提交需另行授权。

### 6.1 代码落点

| 职责 | 优先检查的位置 | 改动边界 |
| --- | --- | --- |
| 构造与公开面 | `marivo/analysis/dsl/population.py`、`marivo/analysis/session/core.py`、`marivo/analysis/_public.py` | 统一键形状、消费参数、公开类型及错误；不另建 facade |
| 惰性定义与 Deferred | `marivo/_definition/{relations,expressions,deferred,observations,graph}.py`、`marivo/semantic/_deferred_semantics.py` | 分配记录迁移、per 语法、一般计数依赖；保留闭合类型 |
| 来源绑定 | `marivo/analysis/dsl/{frame_sources,source_capture}.py`、`marivo/analysis/binding/{observe,coordinates,membership}.py` | 输出域、样本域和贡献分配各归其位；删除隐式计数属性读取 |
| 编译与执行 | `marivo/analysis/compiler/{populations,observation,original,graph_plan}.py`、`marivo/analysis/materialization/{population_preparation,population_relations}.py`、`marivo/analysis/methods/local.py` | 复用完整键、依赖调度和去重；不新增执行路径 |
| 披露 | `marivo/analysis/dsl/_population_disclosure.py`、Session 与 semantic owning Help、`marivo/analysis/_scope.py` | 删除原始 memberships，解释消费规则及 count 对象 |

表中是检查起点，不要求机械修改每个文件。先追踪真实生产者和消费者，再确定文件范围。
公开参数沿用具体的闭合类型；不靠 Any、宽泛 cast 或 type ignore 把新形状送入旧分支。

### 6.2 删除与文档迁移

- 删除 identities 导出、Help 目标及当前示例；不保留兼容包装。
- 删除 Population.memberships 及对应 Help/快照。
  先把有用的捕获事实迁至 Grouping.show / 观察和样本定义摘要，再移除旧属性。
- 清除 Population 上的 membership 传播与集合比较；观察/样本仍保留真实分配依赖。
- 删除 count 隐式读取分类属性的逻辑，不把旧例子的结果变化仅改成新快照。
- 更新受影响的 current Python/DSL/执行合同、类型用例、最小示例，以及
  `site/src/content/docs/docs/latest/` 和 `site/src/content/docs/zh-cn/docs/latest/`。
  历史验收记录保留原事实；不批量重写所有历史版本文档。
- 核对 packaged skills 是否真正包含受影响的工作流判断。本文不授权编辑它们；
  仅在确需修改时列出具体文件和段落取得明确授权，不为签名迁移重复写技能说明。
  AGENTS.md 不需要改动。

### 6.3 保存与兼容

目标不改变完成 Frame 的 axes/values/presence、Store 13 / frame_output v2 数据格式或
Artifact 的终态身份。不保存可执行 Population 计划，不增加恢复解释器。
定义指纹可以因真实合同变化而改变；旧来源事实继续作为原结果的不可执行审计记录。

不预设 Store 升级或历史迁移。若实现发现确实改变了闭合保存格式，应先说明具体字段与
必要性再修订合同，不能用旧版本号承载新格式；这不是本计划的默认工作。

## 7. 必要验证，不建测试矩阵

优先修改现有测试和共享 fixture。同一个语义见证只在合适的一层验证，
不把每个类型、方法、provider、载体和错误输入作笛卡尔组合。

| ID | 必要见证 | 主要复用位置 |
| --- | --- | --- |
| V1 | 字段＋完整复合身份的相关字面元组；去重/Null；带锚点字面身份能与同锚点 members 组合 | `test_grouping_inputs.py`、`test_frame_population_construction.py`、已有 members 版本 fixture |
| V2 | 非法 where 字段谓词产生结构化错误；classify interval 在构造拒绝；仅身份 classify 不新增来源存在检查；接收者 Population.show 区分 and/or 条件 | 现有公开输入与 Population 测试，每个修复一个回归断言 |
| V3 | 一个现有 fanout fixture 同时覆盖相关双字段与独立规则、重复链接去重；显式域保留无事实桶，per 使用同一规则后结果不变 | `test_population_membership.py` |
| V4 | 一般 tuple count：标量总数、按已有字段分组、零轴/空域；显式 classify 的成员计数保持原数值；事实 where 不改分母，时间广播不重选域 | `test_observe_regressions.py`、`test_query_results.py` |
| V5 | 沿用固定 Frame Top 后来源变化仍保留旧键的测试；选一个实际变更链路继续验证 compute/persist/恢复，不新增一套载体矩阵 | `test_frame_population_bridge.py` 及现有恢复测试 |
| V6 | 唯一导出、Help 参数/属性、示例与 latest EN/ZH 文档一致；移除入口无残留生产消费者 | 现有 surface、typing 与文档检查 |

V3 的独立数值见证继续使用已有例子：相同值的不同事实各自贡献，
重复关联不重复贡献；相关桶不能变成独立乘积。V4 同时检查完整键与计数，
不能只比较总和。无需另建随机测试框架、专用模型项目或大规模 Agent journey。

执行顺序：

1. 每阶段跑实际受影响的 focused tests、修改 Python 模块的 typing 和 lint。
2. 修复失败后只重跑受影响范围；记录命令和失败原因即可，不建立额外证据系统。
3. 最终候选跑一次未缩小的 `make check-agent`，包含默认测试与 API 文档；
   失败或后续修改相关代码时再按影响重跑，使最终结论对应最终源码。
4. Runtime 仅在实际改动某 provider 的物理执行/键传输路线时跑该路线已有测试。
   沿用合格路线不要求重跑全部 provider，不为普通重构运行 release-check、启动 MinIO
   或执行新一轮独立全旅程验收。未跑的 Runtime/release 不记为通过。

收尾条件：P1–P4 的目标接口与语义一致，V1–V6 有对应检查结果，最终 broad gate 通过，
已发现的合同/数值问题已修复或明确列为阻塞。独立代码审查聚焦这些改动；
只对实际发现做修复复核，最多两轮，不因团队模式增加验证矩阵。

## 8. 当前证据与实施记录

本设计之前的只读审查在上述基线完成：

- 五个相关测试文件共 81 项通过，属于本地现状证据，不是本设计验收。
- 公开调用已复现：Population.where 接收字段谓词发生递归错误；
  classify 接收 interval 后执行断言；相同坐标域因 membership 不同不能 union。
- 已复现：带锚点 members 不能直接与无锚点 identities 作集合组合；
  仅身份 classify 对缺失身份增加来源读取；Predicate.show 无法区分 and/or。

起草轮文档检查只验证引用、示例语法、决策一致性和差异，不代表新接口已实现。
实施后在下表追加实际结果，保留原始失败与修复原因，不把未运行项写成已通过。

| 阶段 | 候选/范围 | 实际检查与结果 | 未解决项 |
| --- | --- | --- | --- |
| 文档 | `da81a5a447c9` 上的 proposal | 5 段 Python 示例语法、3 个相对引用、围栏及空白检查通过；完成合同自审 | 实现未开始 |
| 实施 | 同一基线上未提交工作树；最终生产文件哈希见实施记录 | 独立审查两轮关闭唯一 P2；独立数值验收通过；最终 make check-agent 4772 项测试及静态/API 文档通过 | 无未解决项；未运行 Runtime/release，未提交 |
