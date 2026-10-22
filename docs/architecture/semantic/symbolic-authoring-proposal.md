# 符号表达式与 loader 统一绑定设计

状态：直接 `ms.ref.*` 符号方案已完成本地实施与验收，两轮实现审查无剩余 P2+；证据见[实施账本](symbolic-authoring-progress.md)。日期：2026-10-22。

原设计基线：`8cd8e08c9345eb28ff36ee55d089e395a93ed452`；本次重新核查代码基线：
`d288fcdd1ab2010c22334e22d83d6d7edaa745e1`，包含当前工作区相关消费者。
本设计已获用户完整实施授权；当前实现、测试、Help、文档及已明确授权的技能条款已同步迁移。
实际基线、独立审查与证据见[实施计划](symbolic-authoring-implementation-plan.md)和实施账本。
此前包装式方案的 R1/R2 不作为当前实现通过证据。既有时间过滤优化工作仍不在修改范围。

当前合同仍由[表达式](expressions.md)、[对象模型](semantic-object-model.md)、
[加载与校验](loading-validation-introspection.md)拥有。

## 1. 目标与决策

作者直接组合符号 Deferred；loader 在完整加载范围内解析、校验和冻结；Catalog
只交付闭合值。删除作者显式调用 `ms.finalize()` 和 `finalized.resolve(ref)` 的步骤。

| 编号 | 决策 |
| --- | --- |
| D01 | Entity、物理字段、Relationship 构造器返回包含 typed symbolic leaf 的原生 Deferred；命名 Measure/Metric 仍返回 Deferred |
| D02 | `ms.ref.*` 全部返回原生 Deferred 符号；现有 Ref/ExactRef 仅作为内部身份与版本事实，保持其比较、哈希和 wire 合同 |
| D03 | `ms.ref.<kind>(path)` 是唯一公开 typed lookup 构造入口；前向引用直接组合，不新增 `ms.symbol` 或作者转换步骤 |
| D04 | 装饰器立即捕获受限语法和当时的符号对象；loader 不调用作者函数、不重新读取 globals、不回放模块 |
| D05 | 命名身份、依赖与循环先确定，再按依赖顺序绑定；不以文件发现或发布遍历顺序决定含义 |
| D06 | loader 创建新的闭合 recipe；原始符号模板保持不可变，不能被第一次 load 填充或封存 |
| D07 | Catalog `.get()` 的 Entity/Relationship 仍为闭合对象，字段和 Metric 仍为闭合 Deferred；身份导航字段返回 lookup symbol，不能冒充闭合计算值 |
| D08 | 复用现有语义 consumer、定义图、数值 lowering 和执行器；符号层不实现第二套数值语义 |
| D09 | 同步处理已支持的 Grouping 和无 Session owner 的 Population 程序；捕获/加载不读 source，不限制其执行时是否需要 source |
| D10 | 一次切换公开 authoring surface；不保留 finalize/resolve 的别名、空操作、旧模式开关或双读路径 |
| D11 | 公开身份输入/输出在各自边界转换；内部 registry、依赖图、持久化及 equality/hash 不使用 Deferred |
| D12 | 以 Ref 为键的公开 preview scope/source bindings 改为有序 typed symbol/value 对，内部仍规范化为身份映射；不把表达式对象当字典键 |
| D13 | 必须将 Population/Grouping 的共享纯构造逻辑下沉到 `marivo/_definition/population_builders.py`；公开适配和执行仍留在 Analysis，见 §5.2 |
| D14 | 原生 Deferred leaf 用十种 `NewType` 名义类型标注身份角色；复合表达式仍为 Deferred，不继承 Ibis Final 类、不引入运行时 wrapper，见 §3.3 |
| D15 | 保留原生 Deferred；每个声明/参数捕获边界立即运行共享 recipe 白名单，loader 再检查闭合语义。普通 Python 属性访问不要求立即失败；该取舍已由用户确认，见 §5.1 |

本次不增加任意 Python 延迟执行、隐式业务定义、自动补全依赖、字符串短名解析、
自动热加载或新执行引擎。未定义的 Python 变量和循环 import 仍按 Python 规则处理。

## 2. 当前代码与需要移动的职责

| 当前拥有者 | 当前行为 | 目标变化 |
| --- | --- | --- |
| [refs.py](../../../marivo/refs.py)、`semantic/__init__.py` | `ms.ref` 直接 re-export 内部 `_RefFactory`，返回 hashable Ref | 内部身份工厂保持；公开 facade 将其键包为符号叶子，不全局替换内部 `ref_factory` |
| [_authoring_decorators.py](../../../marivo/semantic/_authoring_decorators.py) | 物理声明返回 Ref | 记录物理 IR 后返回对应符号 Deferred |
| [_authoring_context.py](../../../marivo/semantic/_authoring_context.py) | `_push_ir` 拒绝 finalize 后声明；`_value_ref` 消费已解析字段 | 删除人工关闭状态；单独提取 native symbol 的身份，不消费语义 |
| [_expression_binding.py](../../../marivo/semantic/_expression_binding.py) | builder 拒绝 Ref；`construct_*` 立即调用 consumer | 受限 AST 捕获不变；允许受控符号 recipe，完整语义校验延后 |
| [expressions.py](../../../marivo/semantic/expressions.py) | `declare_measure` 立即计算权限，`finalize` 创建冻结物理范围 | 权限计算提取为共享绑定步骤；删除公开冻结中间结果 |
| [deferred.py](../../../marivo/_definition/deferred.py) | `SemanticInput` 已含 ExactRef/root/unit/permission/time | 增加仅含 typed identity 的符号叶子；闭合叶子仍用 SemanticInput |
| [loader.py](../../../marivo/semantic/loader.py) | `_publish_declarations` 消费已闭合值并依次发布 | 先索引全部声明、建立依赖，再绑定和发布 |
| [_compiled_state.py](../../../marivo/semantic/_compiled_state.py) | `build_compiled_state` 复制并 freeze registry | 保持最终状态唯一出口，拒绝残留符号或待绑定参数 |
| [catalog.py](../../../marivo/semantic/catalog.py)、[source_health.py](../../../marivo/semantic/source_health.py) | 多处严格 `type(value) is Ref`，结果字段也暴露 Ref | 输入按 symbol 的角色提取键，输出按字段生成 lookup symbol，内部索引继续持有键 |
| [preview_scope.py](../../../marivo/semantic/preview_scope.py) | 公开 scope/source bindings 用 Ref-keyed Mapping | 改为 pair sequence，按内部键查重与校验范围后生成现有内部映射 |
| [datasource/authoring.py](../../../marivo/datasource/authoring.py)、[manage.py](../../../marivo/datasource/manage.py)、[inspection.py](../../../marivo/datasource/inspection.py) | spec.ref 生成内部 Ref，管理/检查入口接收它 | spec.ref 暴露 datasource symbol，公开入口解包；不引入 datasource → semantic 的反向依赖 |

### 2.1 Analysis 消费者清单

按实际接收类型区分迁移边界。不能根据搜索到 `.ref` 就批量改为 symbol，也不能让
公开 Relationship 作为内部 `RelationshipParameter` 子类携带 symbol 属性进入定义图。
现有 `expressions.Relationship(RelationshipParameter, ...)` 必须改为保存私有闭合
parameter 的公开外观；Entity 同样保存私有 ExactRef。公开导航生成 symbol，内部
consumer 取得原闭合键/parameter，保留版本、端点、基数和时间事实。

下表路径均相对于 `marivo/analysis/`；是实施的起始调用清单，最终按真实类型复查：

| 文件 / 入口 | 当前输入事实 | 实施动作 |
| --- | --- | --- |
| `session/core.py`：`Session.members` | 公开 Entity 的 `.ref`，用于 scope、predicate root、AxisBinding 和 SourceIdentities | 改用 Entity 私有 ExactRef consumer，保持当前 Catalog 查找及原捕获事实的使用位置 |
| `dsl/frame_population.py`：`population_from` | 公开 Entity 的身份、版本化检查和轴；另有内部 FieldRead | Entity 部分迁移；FieldRead 的 ExactRef 保持 |
| `dsl/population.py`：identity、membership、image/preimage、路径验证 | 公开 Entity/Relationship 与内部参数目前共用访问形态 | 公开适配器先提取 ExactRef/RelationshipParameter；中立 builder 只收闭合事实，不传公开对象 |
| `dsl/source_capture.py` | `consume_field` 后的 FieldRead、identity/entity ExactRef | 保留内部访问；验证 public-to-private 转换在 consumer 前完成 |
| `_scope.py` | AxisBinding、FieldRead、PopulationProgram 及其内部关系路径 | 保持键渲染；防止路径混入公开 Relationship，而非重写所有 `.ref` |
| `binding/coordinates.py` | ExactRef、FieldRead、contribution root | 保留内部键比较、查找和重新绑定逻辑 |
| `binding/windows.py` | time/field ExactRef、DeclaredMetricRef；另有用户指引文本 | 保留内部索引；校对 readiness 示例可消费新的公开 selector |
| `observation/relationship_binding.py` | TargetRelationshipContract 和内部 Ref 路径 | 保留；不将内部候选路径改为 Deferred |
| `materialization/population_relations.py` | AxisBinding、内部 RelationshipParameter | 保持读取；确认公开子类拆分后路径只存内部 parameter |
| `materialization/population_preparation.py` | PopulationProgram、FieldRead、关系参数和 sidecar 键 | 保持内部事实及 source 准备，不引入 symbol 解析 |
| `materialization/population_condition.py` | contribution.root ExactRef | 保持内部访问 |
| `_capabilities/catalog_inputs.py` | 复用 Semantic registry 与公开 readiness 示例 | 对齐 disclosure，不能把文本示例计为 runtime 键消费者 |

`semantic/expressions.py` 的 Entity.count/展示和 FieldMetadata 路径访问也须使用私有
事实；对应抽取归 S1b，Analysis 入口归 S5，内部定义路径检查归 A18/A20。
这些调整保存现有 scope、owner、版本和执行规则；闭合边界统一由 §7 拥有。

当前 `finalize()` 还会封闭 prior contexts 中的贡献根。删除它后，跨文件补充同一
Entity 的字段应由完整范围校验；重复定义依然报错，不提供最后声明覆盖规则。

## 3. 作者接口与返回值

### 3.1 构造器矩阵

| 入口 | 目标返回值 | 符号角色 / 限制 |
| --- | --- | --- |
| `ms.entity(...)`，含受限 Ibis decorator 形式 | `EntitySymbol` | Entity identity；允许 `.count()`，不是普通数值行 |
| `ms.dimension_column` / `@ms.dimension` | `DimensionSymbol` | categorical field；不能仅因 cast 自动成为 Measure |
| `ms.time_dimension_column` / `@ms.time_dimension` | `TimeDimensionSymbol` | TimeField；时间角色在绑定时校验 |
| `ms.measure_column` | `MeasureSymbol` | numeric physical field；保留声明的 unit/additivity/status-time |
| `ms.relationship(...)` | `RelationshipSymbol` | Relationship 参数；只允许在关系路径等合法参数位置使用 |
| `@ms.measure(...)` | `Deferred` | 带命名记录和待验证断言的行表达式 |
| `@ms.metric(...)` | `Deferred` | 带命名记录的完成归约表达式 |
| `ms.ref.entity/dimension/time_dimension/measure/metric/relationship` | 对应 kind 的 Symbol 名义类型 | typed lookup leaf；相应表达式/参数角色与构造器一致 |
| `ms.ref.domain/datasource/period_calendar/temporal_set` | 对应 kind 的 Symbol 名义类型 | 仅用于对应身份参数和导航的 lookup leaf；不能归约或参与数值/字段比较 |
| `ms.domain`、`ms.period_calendar`、`ms.temporal_set` | 对应 kind 的 Symbol 名义类型 | 记录原治理声明后返回同 kind/path 的 lookup symbol；不增加计算能力 |
| `ms.calendar_grain`、parse/versioning/additivity helpers | 各自现有具体类型 | 语法事实立即校验，依赖事实在绑定时验证 |
| datasource 构造器 | 原具体 spec 类型 | spec 本身不改类；其 `.ref` 改为 datasource symbol，管理/检查入口配套接受 |

上述 Symbol 名称仅为 §3.3 的静态名义类型，运行时均为原生 Deferred。
不增加公开运行时 Symbol 类、表达式状态开关或 Marivo `.resolve()` 方法。symbolic/closed 是
内部闭合状态；公开语义角色仍由 Help 说明。Deferred 自身不新增 Marivo `.show()`。

`ms.ref.<kind>` 每个工厂的签名为 `(path: str) -> <Kind>Symbol`，立即复用当前 kind/path
形状校验；不是可输入任意 kind 字符串的通用 factory。所有十种 kind 都统一返回符号，
避免 domain/datasource refs 与 field refs 表面同族、运行时协议却不同。
工厂不要求 loader context，不检查目标是否存在，不注册定义，不读数据、不读取环境。
未被导出的匿名符号本身不是待发布定义；只有被收集声明消费的 recipe 才形成依赖。

字段运算是 Python 运算符重载构造的 Ibis Deferred recipe；不是执行任意 Python，
也不是立即生成绑定 Ibis Table 的表达式。`status == "paid"` 是谓词构造；两个符号
之间的 `==` 也不是身份判断。Python bool/if、集合去重、字典键，以及 `in`、
`list.index`、`list.count` 不属于符号身份操作合同。`a in (a,)` 可能因 Python 的
对象同一性捷径为 True，而同路径的另一个符号 `b in (a,)` 会触发 Deferred bool
错误；前者偶然成功不构成支持。测试应核对内部键或闭合结果，不用容器成员判断符号。
Domain/Relationship 等不能计算的角色，即使 Ibis 能先构造通用节点，也必须在受控
语法检查/绑定时拒绝不合法操作，不能按所有 Deferred 都是数值处理。

### 3.2 典型写法

以下示例位于正常 domain 模型中，warehouse 已声明：

```python
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(
    name="orders", datasource=ms.ref.datasource("warehouse"),
    source=md.table("orders"), primary_key=["order_id"],
)
amount = ms.measure_column(
    name="amount", entity=orders, column="amount",
    additivity=ms.additive_all(), unit="CNY",
)
status = ms.dimension_column(name="status", entity=orders, column="status")
paid_amount = amount.sum().where(status == "paid")

@ms.metric()
def revenue():
    return paid_amount

# Declared later intentionally: the loader resolves default time over the full scope.
paid_at = ms.time_dimension_column(
    name="paid_at", entity=orders, column="paid_at", is_default=True,
)
```

`revenue` 使用完整物理范围里的默认时间 paid_at。显式 `time=` 优先；同一 Entity
多个默认时间仍是错误。匿名 recipe 不自动发布，Python alias 不创建另一个 Metric。
这里 `.sum().where(...)` 是当前 `_deferred_semantics.py` 支持的 Metric 方法形式，
受单贡献归约等现有约束；不是已退休的顶层 `ms.where(...)`。

### 3.3 物理参数与符号参数

`entity=`、relationship endpoints/keys、`ms.bind`、additivity 坐标、status-time、
period calendar 和 temporal set 字段参数均通过已有入口接收 native symbol。
仅从其 typed leaf 提取 kind/path；不调用 `consume_field` 获取尚不存在的事实。

公开参数接收相应 native lookup symbol 和当前明确支持的已闭合输入；不再接收
内部 Ref 作为另一条公开路径。已闭合输入在物理声明位置按
当前消费范围的 kind/path 查找，仍不要求原始版本相同。物理字段位置不接受
`amount + cost`、`.via()`、`.at()`、匿名行表达式或命名派生 Measure 伪装为物理列。
后者仍缺少 restricted-Ibis sidecar 所需的物理 body/owner，不扩大 `ms.bind` 的能力。

`domain=`、`datasource=`、`calendar=` 等直接接收对应 `ms.ref.*` 或声明返回的符号。
参数提取只允许 native leaf，不能从复合表达式的任意一个依赖推断它“代表”的对象。
例如 `source_check.not_null(amount + cost)` 不能退化成检查 amount。

#### 身份参数的具体类型

在私有共享模块定义 `EntitySymbol = NewType("EntitySymbol", Deferred)` 等十种名义
类型：Domain、Datasource、Entity、Dimension、TimeDimension、Measure、Metric、
Relationship、PeriodCalendar、TemporalSet 各对应一种。它们只给已经通过 kind/path
检查的 leaf 加静态标记，运行时对象不变；不把它们作为公开转换函数或另一个 authoring
入口导出。类型含义在对应 `semantic.ref.<kind>` Help 和签名中说明。

| 参数角色 | 目标签名边界 |
| --- | --- |
| `entity=` / relationship endpoints | `EntitySymbol` 加该入口原已支持的闭合 Entity 类型 |
| `domain=` / `datasource=` | 分别为 `DomainSymbol` / `DatasourceSymbol`，只保留原入口已有的其他具体输入 |
| `calendar=` / temporal-set identity | 分别为 `PeriodCalendarSymbol` / `TemporalSetSymbol`，按各入口保留已有闭合类型 |
| preview pair 首项 | `EntitySymbol` |
| 物理字段身份 | 相应 DimensionSymbol / TimeDimensionSymbol / MeasureSymbol 的精确 union；若原入口支持闭合 Deferred，继续单独验证 native field 和 kind |
| 一般行/Metric 表达式 | `Deferred`；其有效角色及闭合状态由现有 consumer 校验 |

错误 kind 的 identity symbol、普通 Deferred 不能静态传给纯身份参数；运算后返回
普通 Deferred，不传播 leaf 标记。命名 Measure/Metric 声明返回表达式 Deferred，
不因已命名就变成可传物理参数的 leaf。闭合字段仍是 Deferred，静态类型不能证明
它是否为物理列、是否闭合；保留该既有运行时检查，不声称获得完整表达式类型系统。
这不是批准宽泛参数的例外，也不靠 `Any`、`cast` 或 `Annotated` 伪造 kind 区分。
Ibis Deferred 是 Final，不能采用 `EntitySymbol(Deferred)` 运行时子类。

### 3.4 公开符号与内部身份的边界

内部 `Ref[Kind]` 仍保存 kind/path 并支持 boolean equality/hash；`ExactRef` 保留版本。
从 `marivo.refs` 导入内部 factory 的编译、执行和存储模块继续获得身份键。单独替换
`marivo.semantic` 的公开 `ref` facade，移除 `ms.Ref` 导出和公开 Ref 参数/结果声明。
不要直接改内部 `_RefFactory` 的返回值，否则 registry 查找、set、dataclass 比较与
Pydantic/wire codec 会同时落入 Deferred 的表达式协议。

公开 factory、symbol leaf 和 native-key 提取放在最小私有共享模块（建议
`marivo/_symbolic.py`）；只依赖内部 refs 和 Ibis Deferred 基础，不 import temporal、
datasource、Semantic 或 Analysis。现有 `_definition/deferred.py` 已依赖 `_temporal`，
因此不能把低层 temporal 所需的 symbol 工厂反向放进该完整 inspector 模块。
完整 recipe inspector 消费这个叶子；datasource、temporal 和 Semantic 通过同一
共享模块转换，内部 `refs.py` 不为键比较而执行绑定。该模块不增加公共包装类型。
提取方法是私有、kind-dispatched 的函数；不是公开 `.key`/`.path`/`.resolve` getter。
工厂的静态 kind 区分遵循 §3.3；运行时仍由 sealed leaf 和 owning consumer
验证，错误的 expected/received/repair 必须具体。名义类型不代表运行时绑定证明。

| 边界 | 目标合同 |
| --- | --- |
| `catalog.describe(ms.ref.metric(...))` | 接收 native lookup symbol，提取内部键，在该 Catalog 范围查找；不要求先闭合该符号 |
| readiness/preview/preview_many/source_health | 接收原已支持的 Catalog entry/闭合值及相应 lookup symbol，保持各自 physical-only、Metric preview、kind、membership 与 entry ownership 限制 |
| `ms.source_check.*` | helper 捕获相应 field/relationship symbol，立即检查 native kind，执行时仍由 source_health 读取其声明；内部 check records 保存键 |
| `md.test/inspect/raw_sql` 等目前接收 datasource Ref 的入口 | 接收 datasource symbol 并在其当前操作范围查找；原先支持的 datasource name 字符串仅在原入口保留，不扩大 Semantic 的字符串输入 |
| `.ref`、`.refs`、parents/children/reads、readiness 的 `analysis_ready_inputs` 等公开身份字段 | 暴露 native lookup symbol 或有序 tuple；内部 backing records 继续存键；不向用户泄漏可哈希键作为第二条构造路径 |
| Catalog `.get()` | 保留唯一短名/完整路径字符串输入及现有返回族；不额外增加 symbol 输入重载 |
| `.show()`、repr、结构化 error 的身份文本 | 由内部键稳定渲染，继续显示已有 kind/path/版本事实，不通过 Deferred 的动态属性获取 |
| 存储/JSON/Pydantic payload | 保留当前 canonical key/ref payload、版本字段和 schema；反序列化到内部键，Python 公开对象边界才暴露 symbol |

本表只适用于语义 `Ref/ExactRef` 身份字段；Analysis ArtifactRef、datasource 物理对象
定位字符串等同名 `ref` 不在本次变更内。内部调用直接使用私有键消费者，不绕行公开
facade 再解包，也不为了内部复用而让公开函数兼容 raw Ref。

`.ref`/`.refs` 是导航用的未绑定选择器，不是 `.get()` 返回的闭合计算结果。调用
`catalog.describe(selector)` 是明确的 scope 查找；将未绑定 selector 传给普通
Session 执行不触发隐式全局解析。旧闭合值的捕获事实也不因为公开 `.ref` 可用于
新查找而自动刷新。ExactRef 的版本继续在内部 provenance、现有展示和 wire 中保留；
`.ref.version` 不是受支持的 recipe 或身份读取；原生 Deferred 仍会构造该属性节点，
其报错边界和纯 Python 迁移风险见 §5.1。

公开 metadata/check 结果如果需要 equality、hash 或序列化，应读取私有身份 backing
fields，不能对 Deferred 属性使用自动 dataclass equality/hash/asdict。结果不新增
通用 identity API；作者依赖旧 `ref.key/path/kind` 的调用需改用现有 describe/.show
或已知完整路径。上述是有意的公开 API 变化，不宣称 Python 对象协议行为保持不变。

### 3.5 不能保留 Ref-keyed 公开 Mapping

当前 PreviewScope 和 PreviewSourceBindings 的键是精确 Entity Ref。Deferred 不承担
身份哈希/比较，因此不把签名机械替换为 `Mapping[Deferred, ...]`。目标公开形态为：

- 多 Entity scope：`Sequence[tuple[EntitySymbol, AuthoringScope]]`。
- source bindings：`Sequence[tuple[EntitySymbol, Mapping[str, QueryParamScalar | QueryParamScalarList]]]`。

每对首项必须是 native Entity lookup symbol；立即按内部键检查 kind、重复、完整
依赖覆盖和多余输入，再生成现有内部映射。一个 Entity 的 scope 仍直接传单一
AuthoringScope，保留现有“单 Entity 不接受映射集合”的规则；source_bindings 的单项
仍使用 pair sequence。这保留 `preview_scope.py` 当前的不对称合同：scope 本来允许
单个 AuthoringScope，source_bindings 本来始终显式按 Entity 寻址；后者的外层身份与
内层业务参数字典分属不同角色。本次只替换身份映射载体，不新增隐式 Entity 选择
或单 Entity 参数字典重载。业务参数字典保持原状，不增加通用 binding builder。

```python
scopes = (
    (ms.ref.entity("sales.orders"), order_scope),
    (ms.ref.entity("sales.customers"), customer_scope),
)
preview = catalog.preview(revenue, scope=scopes)
```

这只是身份映射的公开载体变化；row/time guards、缺失/无关依赖、重复拒绝、连接前
校验及序列归一化后的语义保持。CLI/持久化输入继续用现有字符串或 ref payload
协议，在边界规范化，不序列化 Deferred。旧 Ref-keyed Python Mapping 不做双读。

## 4. 前向引用与 Python 捕获

```python
margin = ms.ref.measure("sales.orders.margin")
orders = ms.ref.entity("sales.orders")

@ms.metric()
def margin_per_order():
    return margin.sum() / orders.count()
```

对应物理定义和命名 margin 可以在同一加载范围的后续文件声明。完整路径精确匹配，
不根据变量名、当前文件、source table 或相似名称猜测。引用缺失、kind 不匹配、
循环依赖都在 load 返回 Catalog 前失败。

普通顺序依赖仍用变量和 import；尚不可取得或跨文件按身份引用的定义直接用
`ms.ref.*`。factory 和对应构造器产生相同角色的符号叶子，没有 wrapper 或
装饰器专属的自动转换规则。factory 调用位于模块级，纯 body 捕获已构造的符号，
不因此允许在 body 内创建声明、任意调用 factory 或读取 Catalog。

装饰器捕获当时的值和符号对象，并保持当前 AST 限制：零参数、可选 docstring、
fresh locals、一个最终 return；不执行任意 helper、控制流、import 或 Ibis Table。
后续 `amount = other_amount` 不改变先前 recipe。符号身份依赖完整范围，Python
变量绑定不延迟。函数源必须仍可检查，不能用 loader 后置回调规避语法校验。

跨 domain、外部 model root 的引用只在本次实际加载/选择范围内解析。保留现有
domain 选择和组织规则，不为修补缺失 symbol 扩大加载范围或隐式 import 模块。
同名不同 root 不覆盖；现有 Python 模块缓存限制不因本提案而消失。

## 5. 捕获态、绑定态和辅助值

### 5.1 私有表示

复用原生 Ibis Resolver recipe。新增一个私有 typed lookup leaf，保存内部 Ref 与诊断
位置；不保存伪造的 ExactRef、空权限、占位默认时间或可变 loader 指针。
命名声明在捕获阶段保存原始 unit/additivity 断言；闭合阶段才生成当前
DeclarationRecord 所需的规范化事实。使用封闭的捕获/闭合记录变体，不堆叠可选字段。

结构检查允许受控符号，完整 `inspect_deferred`/语义 consumer 对外仍要求闭合。
共享 recipe 遍历和操作白名单，禁止新增独立的可执行数值 AST。每个声明捕获点
（纯 decorator 的捕获结果、物理声明中的符号参数）以及受控 helper/公开参数入口
在保存 recipe 前立即检查完整 args/kwargs/容器子树。命名或模块级组合被收集为
声明时也运行同一检查。合法操作的 arity、常量参数形状、leaf kind 和可静态确认的
receiver 角色当时验证；依赖真实字段/root/unit/time 的检查才留给 loader。
结构检查不查目标存在性，不影响前向引用；loader 对捕获记录复用相同检查后绑定。
错误保留声明文件/行及 recipe 子路径；模块级对象只有消费位置时不虚构其创建行。
symbolic fingerprint 只能用于本次编译的结构去重，不能替代 §7 的闭合计算身份。

**错误时机与已确认的合同边界（C1/P2 已闭合）。** 保留原生 Deferred，错误保证位于
声明和参数捕获边界；普通 Python 属性访问不要求立即失败。当前安装的 Ibis Deferred 是 Final；
它的 `__getattr__` 直接构造 Attr，不委托内部 Resolver leaf。因此 sealed leaf 无法
拦截 `.key/.path/.kind/.version`，也无法让 `amount.summ()` 或 `orders.amount` 在
属性访问那一刻失败。上述节点进入声明/参数捕获边界时必须得到 structured repair，
例如要求显式声明字段或用 `catalog.describe(selector)` 查看身份；`.ref.path` 等
进入这些边界也按同样规则拒绝，不等到完整 loader binding 才第一次检查。

`f"{ref.path}"` 可在纯 Python 中变成普通字符串，随后已无法可靠恢复来源；未消费的
匿名错误 recipe 也没有捕获边界。这两类行为不在错误保证范围内；字符串若随后传入
公开参数，只按该参数现有类型/值规则验证，不追溯它是否由符号格式化而来。
不扫描最终字符串、不全局 monkeypatch Ibis，也不新增运行时符号 wrapper。
用户已明确接受此取舍，C1/P2 在设计层闭合；捕获边界校验仍是 S1/A21 的必验要求，
不能因此延迟到 loader 才检查。仓库旧身份属性调用仍在 S6 定向清理，并在 Help 和
迁移说明中披露这个边界；设计闭合不代表实现或第三方调用已完成迁移。

同一次绑定使用显式范围及局部 memo；memo 不跨 load，不按 Python 全局注册表解析。
对纯闭合输入的本地 Measure/Metric，继续立即运行当前语义校验和断言规范化。
若存在任一未绑定依赖，则保留完整捕获记录，由 loader 一次绑定全部依赖位置。

### 5.2 必须覆盖的依赖位置

不能只替换 arithmetic receiver。绑定须遍历二元/一元、分支、predicate、tuple、
call args/kwargs、time、where、per、weighted mean、via、at、within、window、
Measure permission 坐标及无 Session owner 的 Population/Grouping 参数。
保留完整 predicate 与分支依赖，即使布尔常量或算术常量能简化结果。

| 辅助入口 | 闭合输入 | 含符号输入的目标行为 |
| --- | --- | --- |
| `mv.identity` | 现有 Grouping | 捕获受控 Deferred 参数 recipe，绑定后生成同一 Grouping |
| `mv.membership` | 现有 Grouping | 捕获完整 fields、路径、unmatched；绑定后复用当前校验 |
| `mv.coordinates` | 现有 Population | 捕获有序 axes 和有限完整 keys；绑定后生成同一有限 Population |
| Population 的 `.axes` | 现有 tuple | 允许捕获 `.axes` 及常量整数索引；禁止动态属性和任意索引调用 |
| Population 的 `.project` | 现有 Population | 捕获现有轴的选择/重排，绑定后复用完整元组去重及禁止读取新属性的规则 |
| `.union` / `.intersection` / `.difference` | 现有 Population | 捕获两个域；绑定后验证相同有序轴，沿用去重和 owner 合并规则 |
| `mv.product` | 现有 Population | 捕获有序因素，含已闭合 CalendarAxis/TimeScope；保持不同轴要求、完整笛卡尔积及空因素语义 |
| `.classify` | 现有 Population | 捕获单身份轴及直接分类字段；绑定后复用 owner、路径和时间轴限制，生成现有 source program |
| `.image` / `.preimage` | 现有 Population | 捕获接收域、目标域、完整有向路径及现有 at 参数；保持身份键、端点、版本锚点和去重规则 |
| `.where` / `.count` / `mv.in_population` | 现有结果 | 在当前合法位置捕获对应 recipe，保留相关元组、完整 predicate 和 owner |

产生 Grouping/Population 的入口，其目标签名明确包含对应 `Grouping | Deferred`、
`Population | Deferred` 结果，不使用 Any；count/in_population 仍产生 Deferred。
仅含符号输入时返回相应角色的 Deferred；闭合调用保持当前结果类型。
参数 recipe 采用有限操作标记，由共享定义层描述，现有 source-free builder 负责
闭合转换；Semantic 不得 import Analysis 来寻找运行策略。D13 的下沉为实施必需，
不是可选优化；公开入口仍在原模块。

目标 `marivo/_definition/population_builders.py` 只消费闭合的 ExactRef、FieldRead、
AxisBinding/时间坐标、RelationshipParameter、PopulationDependency 和 predicate
node，返回现有 Grouping binding/PopulationProgram/计数或 membership resolver。
从 `analysis/dsl/population.py` 提取如下纯构造部分，统一供公开闭合适配器和 loader
调用，不复制一份绑定专用算法：

- identity/membership 的轴与有向路径构造；coordinates 的完整 keys 规范化。
- `_axes` 的轴合法性与去重、`_owner/_combined_owner`、`_identity_path` 的闭合事实检查。
- project、set 三操作、product、classify、image/preimage、where 的程序构造与 owner 传递。
- axes/index、count、in_population 的现有定义投影与 resolver 构造；已有中立实现直接复用。

`_axis` 中消费 Deferred/公开 Entity 的适配、公开 Population/Grouping 外观、
`_frame_inputs` 采集/合并、Session/Frame services、source 执行和 Analysis repair
仍留原拥有者。纯 builder 用现有 definition error；Semantic/Analysis 边界各自转成
owning structured error，保留原合法性限制和具体修复信息。Calendar/Interval 的
闭合坐标和既有 `_CoordinateGrouping/_MembershipGrouping` 复用，不复制时间语义。
不把 Symbol/Deferred 检查塞入纯 builder；loader 先绑定参数，再调用它。

有限 keys、路径序列及其他容器在捕获时复制为不可变值；不能在绑定时再次读取作者
提供的可变 list/dict。每个操作只有自己的 typed 参数记录，不接受任意对象递归冻结。

本表是模块级受控构造能力清单，不是新的装饰器调用白名单。Grouping/Population
helper 及其组合继续在模块级构造，再把结果交给纯 Measure/Metric body。尤其当前
`Population.count()` 应先在模块级得到 Deferred，再由 body 返回；不能因为符号
Population 的 Python 类型变成 Deferred 就允许其在 body 内作为普通行 receiver。
AST 捕获应按实际语义角色沿用当前调用边界，而不是仅按 `isinstance(Deferred)` 放行。
这不限制现有合法的 `orders.count()`、字段归约或 Metric `.per(...)`。

这是保存已有能力的必要范围：当前
[test_deferred_authoring.py](../../../tests/semantic/test_deferred_authoring.py)
已覆盖 named Metric 的 membership 和 `coordinates(...).axes[0]` 样本。
[population.py](../../../marivo/analysis/dsl/population.py) 中的 project/set/product
以及 classify/image/preimage 生成现有 PopulationProgram，并传递或合并 owner。
发布仍检查完整闭包的 owner 和依赖范围；无 Session owner 的 source program
也可发布。“捕获/加载不读 source”不意味着最终执行只能处理内存 literal keys。
不增加任意 Python callable resolver；有限参数节点不能被用作普通数值叶子。
类型约束、tuple/keys 形状和枚举可立即检查；字段 grain、身份键数和路径事实延后。
所有组合的 owner 都必须保留，不能在参数绑定时清空。Session 所属 Population
仍在发布时拒绝；普通 Session API 收到未绑定值时失败并
指向 load/Catalog 获取流程，不自动绑定，不给用户增加另一种 resolve 入口。

## 6. loader 算法与命名身份

### 6.1 收集和物理范围

1. 按现有 discovery/import 机制执行模块一次，收集物理 IR、restricted-Ibis sidecar
   和命名表达式根。保留声明来源文件/位置及拥有的 domain，不使用临时 alias 命名。
2. 校验物理 duplicate/kind/引用/循环、组织和 datasource；构建完整物理定义及其
   默认时间、坐标、关系事实。复用 registry/assembly 校验，不再创建公开冻结结果。
3. 物理只读绑定视图与最终 registry 分离，禁止过早调用会冻结后者的
   `build_compiled_state`。复用 `_Resolver` 的事实提取逻辑，使其接受显式只读视图；
   该视图是 loader 私有输入，不成为新的状态权威或可持久化快照。

### 6.2 Measure 身份先于表达式绑定

Metric 的路径是 explicit/default domain 加函数名。Measure 的路径仍是贡献根
`domain.entity` 加函数名，不新增 `entity=` 参数，也不让文件路径决定所属 Entity。

为避免“必须先发布才能找到前向 Measure”的循环，先做一个受限的行根推导步骤：

- 物理字段从已验证的 owner 得到根；命名行声明通过捕获对象递归推导根。
- `ms.ref.measure("sales.orders.margin")` 在身份步骤提供待验证的根提示
  `sales.orders`；它不证明 margin 存在，不授予 numeric/unit/additivity 事实。
  此提示依据现有 `refs.py` 的 `_validate_ref_path`：Domain 恰好一个 segment，
  Entity 两个，Measure 三个，segment 匹配 `[a-z][a-z0-9_]*`，domain 不含点。
  必须先复用该校验再取前两段；不对未校验字符串随意 split，不新增宽松域名规则。
- 合法 row 运算传播其有效贡献根；关系 scalar path 从已验证端点计算有效根。
  复用当前操作角色和路径规则，不能用“所有依赖必须同根”误判合法关系读取。
- 没有根的常量不能独立定义 Measure；行根冲突、归约后值作为 Measure 等拒绝。
  直接声明对象互相递归时报告循环；不使用猜测候选或固定点重试来决定身份。

完成推导后统一索引所有物理和命名 Measure。检查重复 kind/path，再检查每个符号
是否实际命中声明，且声明真实贡献根与路径一致。随后才进行完整语义绑定。
引用路径提示不允许把缺失声明“补成”一个实体字段。

两个不同 Entity 可以分别有名为 margin 的 Measure；完整路径区分它们。
同一 Entity 上物理 margin 与命名 margin 冲突；不同文件中两个同路径命名声明也冲突。
一个对象的多个 alias 只收集一次；从其他拥有者导入的声明不在导入处重新发布。

### 6.3 完整依赖图与顺序

索引完成后，从符号、直接命名对象、物理 sidecar 和受控参数中抽取依赖，保留边的
来源位置和参数角色。检查缺失/kind 后执行循环检测和拓扑绑定；稳定排序仅用于
诊断/输出，不能改变解析结果。self-reference 不得在循环检测前从依赖集合删除。

| 依赖方向 | 目标规则 |
| --- | --- |
| 物理定义 → 物理定义 | 保留现有受限表达式、ownership、关系和时间合同 |
| 物理 Ibis body → 命名 Measure/Metric | 不扩展支持；必须有该物理入口所需 sidecar/body/owner |
| 命名 Measure → 物理行输入 / 命名 Measure | 支持，按依赖顺序校验行阶段、根、unit、permission |
| 命名 Measure → Metric | 拒绝 Row/Metric 阶段混合，即使 kind/path 存在 |
| Metric → 物理归约 / 命名 Measure / Metric | 支持；各组件保留独立时间、过滤、贡献根与政策 |
| 命名表达式 → Population 输入 | 拒绝 Session-owned 闭包；无 Session owner 且独立可解析的 literal domain/source program 保留现有支持 |

无需把“所有 Measure 在所有 Metric 之前”变成另一条作者顺序约束。静态阶段检查
拒绝非法边，合法 DAG 的依赖决定绑定顺序。直接对象依赖与 typed lookup 必须落到
同一声明节点；不能复制成两个命名 Metric，也不能因为捕获顺序不同遗漏依赖。

### 6.4 闭合、断言与发布

绑定时把物理 symbol 替换为已有 SemanticInput/Entity count leaf/RelationshipParameter，
把命名 symbol 替换为已绑定的声明 recipe；参数 recipe 生成现有闭合参数值。
随后调用现有 semantic consumer。Measure 的 unit/additivity 校验和规范化复用
`declare_measure` 提取的逻辑，包括继承、单位兼容、同根坐标和不能抹掉 fixed time。

对命名 Metric 保留 DeclaredMetricRef 与完整可追溯 body；对命名 Measure 保留当前
行 recipe/断言语义。不要为方便拓扑排序改变归约、fanout、null/empty 或数值 finishing。
所有发布对象成功后才构造最终 CompiledSemanticState。失败返回带结构化错误的
LoadResult，不交付部分 Catalog，不重试导入；已存在 Catalog 不受失败影响。

命名身份在图排序前确定，ExactRef 的版本在依赖闭合后计算，二者不能混用。沿 DAG
自底向上以已有 canonical definition/computation 规则计算版本，再构造消费者的
DeclaredMetricRef。registry 中命名 Metric 的根保存未包裹自身引用的 body；引用包装
用于依赖边及 Catalog 消费视图，不能让声明自己的版本参与自身 body 的指纹计算。
不得用尚未完成的全 Catalog fingerprint 作为每个节点版本的种子，也不得先使用
临时版本、发布后原地修补。

版本一致性在本次绑定生成 symbol 的 ExactRef/声明引用时验证；不对最终图全部叶子
与当前 registry 做版本相等检查。外部捕获的旧闭合叶子保留自己的事实和指纹，继续
接受现有 scope/kind/root/unit/time 等校验，不因同路径当前定义不同而被拒绝或刷新。
canonical 规则确需变化时必须显式记录，不能静默依赖 symbol token。

## 7. 已闭合输入、指纹与隔离

模型可以引用已有闭合值，但当前 publication 的完整依赖存在性与 kind 检查仍适用。
裸闭合物理值和匿名表达式保留捕获事实，不因进入新模板而自动刷新。已发布 Metric
在消费者冻结范围内的 kind/path 解析继续遵循 `consume_metric_in_state` 的现有合同。
物理构造器身份参数则按当前范围解析，三者不得混为统一“重绑定所有叶子”。

symbol 没有历史版本要求；loader 根据完整当前范围生成实际 ExactRef、root、unit、
权限、time、coordinates 和关系事实。闭合指纹只使用消费的定义/recipe/参数事实，
不使用对象 id、模块加载次序、绝对路径、诊断位置或 symbol 的临时 token。

对同一闭合语义，constructor symbol、`ms.ref.*` symbol 和直接闭合输入应得到相同
计算图身份；符号拼写不能成为额外执行依赖。发布身份与匿名公式身份继续遵循现有
命名协议，不要求它们的所有元数据逐字一致。

同一模板可以分别绑定到 A/B 范围，A/B 的闭合结果互不修改。不增加全局绑定缓存。
严格禁止符号、捕获记录、待绑定 Grouping/Population 进入执行图、Catalog `.get()`
闭合返回值、Store 或 temporal certification 状态。公开 metadata 中的 `.ref/.refs`
lookup selector 不在此禁令内，但不得被当作已绑定事实或写入持久化 payload。
不存在持久化符号的新协议，也不默认升级 Store。
若实现确实改变持久化闭合编码，须单独记录证据与版本决策，不能加静默兼容 reader。

## 8. 错误、发现与不变量

捕获边界报告：非法参数形状/kind、unsupported Python/recipe、进入捕获的旧身份属性；
具体时机和无法保证的纯 Python 用法见 §5.1，不能将“捕获时”写成“任意属性访问时”。
绑定时报告：missing target、重复身份、依赖循环、Row/Metric 阶段、root/unit/time/
permission/路径/Session ownership 错误。保持 owning SemanticError 及 structured
expected、received、refs、原声明位置、依赖链和具体 repair，不退化成 ImportError。

闭合语义继续保证：完整相关坐标；显式多值关系分配；逐层 per 样本；窗口重算原式；
三值 predicate；空输入与 all-NULL 区别；零分母政策；实际 dtype 在执行准备时绑定。
load 不建立数值正确性、source health 或 provider qualification 的证据。

更新现有 `semantic.ref.<kind>` Help，说明其 Deferred 角色、前向引用、合法操作和
不支持身份 bool/hash；不新增 `semantic.symbol`。更新 metadata 导航、datasource、
source checks 和 preview pair sequence 的相关 Help/例子。移除
`semantic.finalize`、`semantic.finalized_declarations` 及其属性/方法路由；不留跳转别名。
Catalog describe/.show 仍拥有已发布事实，Help 拥有静态操作与角色说明。

## 9. 兼容清理范围

| 项目 | 决定 |
| --- | --- |
| `ms.finalize` / authoring 导出 / FinalizedDeclarations / 其 resolve、show、require | 删除公开入口与失去消费者的实现；不保留 deprecation stub |
| `declaration_state`、`LoaderContext.finalized/prior_contexts`、late-capture guard | 删除人工冻结专用部分；共享校验和物理事实提取迁入 loader |
| expressions 内部 `resolve`、`_Resolver`、CatalogValues、catalog_closure | 保留仍有消费者的解析职责；去掉 FinalizedDeclarations 联合分支，不按名称批量删除 |
| 内部 Ref/ExactRef 的查找、序列化、kind/hash/equality | 保留；禁止替换内部工厂为 Deferred 工厂；不机械改 wire schema |
| 公开 `ms.Ref`、Ref 参数/结果、ref.key/path/kind/身份 equality/hash | 移除公开键协议，输入/输出按 §3.4 适配；内部 backing 和显示事实保持 |
| 包装式 `ms.symbol` / `semantic.symbol` 提案 | 已撤销；本次没有新增该入口、兼容 wrapper 或 Help 别名 |
| 物理参数接收 lookup/闭合值 | lookup 改由 ms.ref.* symbol 提供，闭合输入仍按当前范围身份消费 |
| PreviewScope / PreviewSourceBindings 的公开 Ref-keyed Mapping | 按 §3.5 改 pair sequence；私有映射及 wire 保持，不双读旧 Python 形态 |
| 首次符号运算触发自动 finalize、隐式全局 resolver、刷新旧对象 | 不实现 |
| constructors 返回 Ref 的类型声明、手工 resolve fixtures | 切换到新形态；断言返回 Ref 的旧测试替换为角色/绑定行为验证 |
| 完整默认时间、前向 Ibis 依赖、导入一次、指纹和隔离测试 | 保留行为证据，重写触发路径；不能随 finalize 测试整体删除 |
| 现有 retired factories/aliases（aggregate 等） | 不因本改造顺便重构；仅在实际受影响入口内清除旧冻结指引 |
| 当前 Help、CLI、API docs、examples、site latest EN/ZH | 同步更新；显式清理 `semantic/__init__.py` 模块 docstring 的 finalize/resolve 首屏示例；历史 release 文档保留历史归属 |
| `marivo-semantic` / `marivo-analysis` packaged skills、AGENTS.md | 用户已明确授权三个命名文件；相关条款已按直接符号组合、闭合 Catalog 输入和 selector 边界对齐 |

这是有意的 authoring 和身份导航 API breaking change：旧模型的 finalize 调用需删除；
依赖 ref.key 的代码按 §3.4 迁移，不能从 Deferred 动态属性取身份。并非所有 `.resolve`
字符串都是旧 API（例如 Ibis/内部消费者），清理以定义和调用链为准。

## 10. 实施与验收

可验证条件、文件所有权、阶段依赖及证据表见[实施计划](symbolic-authoring-implementation-plan.md)。
`ms.ref.*`、身份输入/输出、preview pair sequence、辅助值的 Deferred 分支、共享
Population builder 与 loader 绑定已经作为同一变更实施。A01–A22 的当前测试依据、
两轮实现审查及最终本地门禁见[实施进度与证据](symbolic-authoring-progress.md)。
此前包装式方案的历史设计审查不作为当前实现证据。实现 R2 已独立确认捕获边界、
additivity、Help/docstrings 和纯身份名义类型问题全部闭合，无剩余 P2+。
本地数值与持久化证据各自记录；本次没有 provider Runtime、安装包或发布验收。
实现中若需要新增第二种符号入口、公共生命周期类或任意 helper 延迟执行，应回到本
设计修改决策，不能作为实现细节悄悄扩展。
