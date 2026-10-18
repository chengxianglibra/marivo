# Marivo 能力暴露体系重设计

Date: 2026-10-18

Status: 已实施公开披露契约。源码环境验证与证据边界见第 14 节；未发布。

观察基线：`d06aa805a8` 及写作时工作区。工作区已有分析执行等改动；本文只新增设计文档，
设计阶段不将这些未提交改动归入本设计，也不声称该观察基线已经通过完整验证。

现行契约参见 [公共暴露规范](../../specs/agent-friendly-public-surface.md)、
[分析设计](../../specs/analysis/python-analysis-design.md)和
[语义概览](../../specs/semantic/overview.md)。本设计涉及的替换在第 11 节明确列出；
下文的旧行为描述指观察基线；已实施能力以当前公开 API 和上述拥有者规范为准。

## 1. 目标与基本判断

目标是让一个只知道“Marivo 是 Python 数据分析库”的 agent，凭项目解释器、业务问题和
已有项目状态，找到所需能力，写出调用，读懂结果并修复错误。无需先学习 Marivo 的帮助系统。

降低认知成本不等于缩短每张卡片。需要同时减少：

- 为开始工作必须记住的概念、入口和特殊规则。
- 找到并理解下一次调用所需的阅读、跳转和重复内容。
- 猜测 API、误解输入来源、混淆逻辑定义与执行结果造成的失败。
- 为修复失败而重新发现已有事实的成本。

总代价可以按“准备成本 + 信息获取成本 + 调用与修复成本”评估。少一页 Help 却多一次
错误执行，不是改进；文字更少但必须读源码才能调用，也不是改进。

设计采用以下决策：

1. **Help 说明能力，`.show()` 解释实例，结构化错误修复失败。** 这三者构成默认认知模型。
2. **CLI 是 shell agent 的默认静态发现入口，Python 是唯一计算与对象组合入口。**
3. **普通 Python 接口的结构自动提取，表达式语法、语义和任务导航由领域所有者声明。**
4. **移除分析对象通用的 action contract。** Query、Population 的实例事实直接展示，
   类型方法从公共接口发现，不再要求 agent 先调用 `.contract().show()`。
5. **保留有独立领域含义的结构化值。** 不因名字含有 Contract 就删除语义定义或持久化契约。

这里的取舍是：用一般 API 发现和实例事实组合调用，放弃维护“每个实例当前可调用什么”的
通用目录。调用本身仍执行真实校验；展示不试图预测整个执行过程。

## 2. 从 agent 的四个问题确定信息所有者

| Agent 的问题 | 唯一事实所有者 | 首选读取入口 |
| --- | --- | --- |
| Marivo 能做什么，这次调用怎么写？ | 安装包的公共 API 与原生 Help 元数据 | CLI Help；已有 Python 上下文时用 `marivo.help(...)` |
| 项目中有什么业务定义，如何取得输入？ | 实际加载的 Catalog 与定义条目 | collection、`catalog.describe(ref)` 和公开获取 API |
| 当前这个对象是什么，已经确定了什么？ | 对象捕获的定义或已提交记录 | 对象 `.show()`，需要计算时读取公开类型化字段 |
| 这次为什么失败，应该修复什么？ | 实际失败的校验与结构化错误 | 异常消息、结构化字段和精确 Help target |

工作流 skill 负责业务判断、权限边界、语义 handoff 和停止条件；它不是第五份 API 手册。
解释器和安装身份由 `--version`、明确指定解释器的命令和 `doctor` 负责。

信息只有一个所有者，可以有多个投影。例如 Help 可以引用错误种类，错误可以链接 Help；
二者不能分别维护同一份参数规则。卡片中的短状态标签也不能成为第二套状态机。

## 3. 入口与通道

### 3.1 CLI 的目标形态

以下为已实施接口；观察基线的 bootstrap-only CLI 已被替换：

```sh
.venv/bin/python -m marivo --version
.venv/bin/python -m marivo help
.venv/bin/python -m marivo help analysis
.venv/bin/python -m marivo help analysis.query
.venv/bin/python -m marivo help analysis.query.select
```

安装的 `marivo` console script 提供同样语法。默认示例使用项目选定的解释器；不假设 PATH
上的 `marivo` 与执行脚本的环境相同。`.venv/bin/python` 是本仓库的示例，不是用户项目的固定路径。

`help` 接受零个或一个完整 target。无需先运行 bootstrap 再拼接 `python -c`；不接受
`help analysis Query select` 等第二套路径语法。`marivo --help` 仍解释 CLI 命令本身。

- 成功时将纯文本 Help 写到 stdout，退出码为 0；默认无颜色、分页器和交互提示。
- 未知、歧义 target 或参数错误写到 stderr，退出码为 2；建议只取自真实公共 target。
- 内部渲染失败退出码为 1，保留可诊断的结构化原因，不将失败伪装成空目录。
- Help 页标识 canonical target 和安装版本，不在每页重复解释器路径及完整环境指纹。
  包来源和解释器详情按需交给 `doctor`；路径差异不改变静态 API 内容。
- Help 不加载项目定义、不访问数据源、不创建 Run、不修改业务状态。库导入和已有本地
  telemetry 不构成业务检查；不得为了生成帮助执行项目模块或调用待描述的 API。

现有 `doctor` 继续承担显式环境、加载和连接诊断。访问数据源的效果由所选选项明确披露。
常规 Help 不要求先运行 doctor，正常执行也不增加固定诊断前置步骤。

首轮不增加 Help JSON 协议、全文搜索服务、CLI 对象句柄或计算命令。固定 target 查询和
有界导航已经足以检验本设计；新入口需要独立的实际需求。

### 3.2 Python Help

```python
import marivo
import marivo.analysis as mv

# query is an existing Query in this execution context.
marivo.help("analysis.query.select")
marivo.help(mv.Query)
marivo.help(query.select)
marivo.help("analysis.query.output_names")
```

这些输入通过同一个原生 registry 解析：`mv.Query` 对应 `analysis.query`，两种 select
查询对应 `analysis.query.select`，`output_names` 的字符串查询对应属性页。
绑定方法只用于识别公共接收类型及方法；
不执行方法，也不插入实例的业务状态。同一 target 的 CLI/Python 静态正文一致。

属性和字段通过字符串 target 查询，由类型页提供链接；不增加 property/descriptor 对象
重载。`marivo.help(query.output_names)` 会先读取属性并传入结果值，不能据此恢复接口身份，
因此不是属性 Help 的查询方式。读取当前值时直接使用 `query.output_names`。

目标态的 `marivo.help` 接受 `None`、已登记字符串、公共 callable 和公共 type。
移除结果、Ref、CatalogEntry、异常实例的专用 briefing 分支：

- 结果实例读 `.show()`；其类型能力读 `marivo.help(type(value))`。
- CatalogEntry 读其原生展示或 details；业务定义读 `catalog.describe(ref)`。
- Ref 是身份/查找键；通过显式 Catalog 操作取得定义，不因查询 Help 自动解释项目状态。
- 异常实例保留原始消息和字段；错误类型说明可用 `marivo.help(type(error))`。

这样 Help 的参数形态不会悄悄改变“静态说明”和“当前对象事实”的含义。删除这些重载属于
公开契约变更，需要与引用者同步，不在 CLI 中另建等价分支。

## 4. Help target 的组织

### 4.1 两种访问路径，共用一套叶子

**任务导航**回答“不知道名称时从哪里开始”。根只介绍核心概念并提供 `authoring` 和
`analysis` 两条路线。authoring 导向 datasource、semantic 和可选 ontology 的拥有者；
analysis 按开始/恢复、获取输入、构造计算、读取结果等实际任务提供有界导航。

**类型与成员索引**回答“拿到这个类型后有哪些接口”。类型页列出公开字段、属性、方法及
对应的精确 target；它不要求 agent 回到任务根。具体 API 页是两条路径共同到达的叶子。

```text
help → analysis → 与问题相关的任务组 → 精确 API
help analysis.query → 公开成员索引 → 同一个精确 API
错误或结果中的精确 target → 直接读取该 API
```

不按内部包、文件顺序或固定数量分页组织业务导航。根不展开全量类型、错误或方法表。
已知 target 可直接访问，任务导航不是强制学习顺序。

每个需要主动发现的能力有一个主要任务归属；相关任务可以交叉链接同一个叶子。
构造器、返回类型和接收方法形成可追踪的获取/消费关系，不为每个方法重复登记任务入口。
所有公共能力都必须从任务入口或公开类型链到达；“可以猜中字符串”不算可发现。

### 4.2 统一的语义地址

target 是 Help 的稳定语义地址，Python 符号是页面说明的实际使用方式。二者分开登记；
agent 不需要判断大小写是否代表类，也不需要将代码表达式转换为 target。

```text
<surface>.<object_or_capability>[.<operation>]
```

- 路径片段全部使用小写 `snake_case`；点表示所属关系，不表示 Python 模块或类。
- 领域前缀为 `datasource`、`semantic`、`analysis`、`ontology`；`root`、`authoring` 等正式全局主题
  作为显式根节点。更深的语义分组可以保留，但必须有独立含义和可发现的父节点。
  无参数 Help 默认选择 `root`；其页头给出的 target 同样可直接查询。
- 对象页解释含义、获取/定义方式和成员导航；操作下沉到该对象之下。`define` 表示定义，
  `from_column` 表示从物理列构造；只在对应能力真实存在时登记，不预留不存在的操作。
- 原生领域拥有 canonical target。普通成员可从已登记的父 target 和公共成员名派生；
  概念、构造方式及表达式语法由拥有者明确命名。禁止将所有 Python 路径转小写后直接注册。
- Python binding 指向该语义地址；实现模块移动、公开类名大小写不决定 Help 地址。
- 字符串只接受完整 canonical target 和正式根主题；不接受代码表达式、大小写自动纠正、
  裸短名、隐式前缀或兼容别名。错误返回真实候选，不自动替换成另一个 target。

以下为目标态映射；Python 写法保留真实大小写，旧 target 的切换见第 11 节：

| Canonical target | 页面职责 | Python 写法 |
| --- | --- | --- |
| `semantic.objects` | 选择语义对象种类 | 无，导航页 |
| `semantic.metric` | Metric 含义、定义方式和表达式操作 | 无单一符号 |
| `semantic.metric.define` | 定义具名 Metric | `@ms.metric(...)` |
| `semantic.metric.where` | 限定 Metric 的贡献范围 | `revenue.where(...)` |
| `semantic.measure` | Measure 含义与定义选择 | 无单一符号 |
| `semantic.measure.from_column` | 从物理列定义 Measure | `ms.measure_column(...)` |
| `semantic.measure.define` | 定义派生行表达式 | `@ms.measure(...)` |
| `analysis.query` | Query 类型与成员 | `mv.Query` |
| `analysis.query.select` | 选择输出 | `query.select(...)` |
| `analysis.query.output_names` | 输出名属性的读取契约 | `query.output_names` |
| `analysis.artifact.id` | Artifact 身份属性的读取契约 | `artifact.id` |
| `analysis.session.get_or_create` | 获取显式 Session | `mv.session.get_or_create(...)` |

对象定义导航统一为 `semantic.objects → semantic.<kind> → 具体定义操作`。当前
`semantic.objects.metric` 的对象说明并入 `semantic.metric`；原 `semantic.metric` 的
decorator 说明迁至 `semantic.metric.define`。不能保留两份相同的对象介绍。

导航必须输出完整 target，调用者复制即可。每个具名页面用 `Target` 标明正式地址，
用 `Kind` 区分 `topic`、`object`、`callable`、`attribute`、`expression`、`error`；Kind 不增加路径层级。
`Python`、`Syntax` 或 `Usage` 表达实际代码，不是可传给 CLI 的 target。例如：

```text
Target: semantic.metric.where
Kind: expression
Syntax: metric.where(predicate)

Target: semantic.metric.define
Kind: callable
Python: ms.metric
Usage: @ms.metric(...)

Target: analysis.query.output_names
Kind: attribute
Python: query.output_names
Value type: tuple[str, ...]
Access: read-only
Meaning: Complete output names in definition order.
Read effects: Captured metadata only; no schema or source reads.
```

语义对象页和公开类型页同属 `object`；各自保留领域定义或类型成员信息。Python 的 `ms.metric`
作为对象传给 `marivo.help` 时解析到 `semantic.metric.define`；字符串 `"ms.metric"` 不属于
target。所有代码绑定均显式或按无歧义规则关联到一个 canonical target，并检查命名冲突。

继承的公共方法按实际公共接收者与拥有者解析。共享 display 方法可以链接拥有者的同一份
文档；有接收类型特有约束的必须保留该接收类型的精确契约。不能只凭底层函数相同就合并。

公开字段与 property 统一使用 `Kind: attribute`，target 为父对象的语义地址加成员名，
例如 `analysis.query.output_names`、`analysis.population.axes`、`analysis.artifact.id`。
不增加 `.attributes` 路径层，也不把 property getter 暴露成独立 callable。类型页列出这些
成员的短说明和完整 target；每个 target 可独立解析到属性读取契约。

属性页展示读取写法、值类型、含义、读写权限、读取效果及适用条件；不展示调用签名或当前
实例值。是否只读与读取是否涉及 I/O 是两个独立事实，由拥有者声明。Help 本身始终不读取
属性值、不执行 getter。属性返回的类型可链接到已有类型页，但不能以返回类型说明替代
该属性的含义，例如 `artifact.id` 不能只链接到 `str`。

闭合类型族的字段还需说明所属变体。例如 `analysis.grain.calendar` 使用公开写法
`grain.calendar`，并注明仅在 `grain.kind == 'semantic'` 时存在；
`analysis.time_scope.calendar` 仅适用于 `time_scope.kind == 'calendar_period'`。
类型页和属性页复用拥有者声明的同一适用条件，不输出私有实现类名，也不读取 getter 来判断。

### 4.3 页面职责

| 页种类 | 必须提供 | 不承担 |
| --- | --- | --- |
| 根/任务组 | 用途区别、下一层真实 target | 完整签名、完整类型表、工作流脚本 |
| 语义对象/角色页 | 含义、定义前的业务决策、构造方式、依赖关系和表达式操作入口 | 从物理结构猜业务意义、伪造同名 Python 类 |
| 类型页 | 定义、获取途径、公开成员短说明及 target | 当前实例是否能成功执行、全套成员示例 |
| callable 页 | 用途、真实签名、参数语义和获取、返回语义、约束、效果、最小示例 | 实例状态、任务推荐、重复全量导航 |
| 属性/字段页 | 读取写法、值类型、含义、读写权限、读取效果及适用条件 | 执行 getter、展示实例值、伪装成方法 |
| 表达式语法页 | 接收语义角色、受支持调用形态、参数及默认规则、输出角色、约束、效果和示例 | 将通用 Deferred/Ibis 接口当成 Marivo 支持承诺 |
| 错误类型页 | 稳定字段和该类错误的修复规则 | 虚构本次收到的值和候选 |

签名与返回类型只显示一次。叶子包含足够的局部解释，只有缺少具体输入或类型知识时才需要
继续跳转。示例明确外部前提，使用标准 imports，不以省略号代替必须输入。

显示预算不允许删除必要约束。类型页列出完整的受支持成员；超过预算应调整实际页面职责或
语义分组，不能静默隐藏成员。实例和表格有自己的有界展示规则，不套用 Help 页预算。

## 5. 自动提取与主动声明

### 5.1 公共范围先确定，结构再反射

公共能力分为可反射的 Python API 与领域声明的表达式语法。前者的范围来自明确的 exports、
公共 namespace、已声明返回类型，以及这些类型的受支持成员。`__all__` 是模块导出的依据，
但不是所有返回对象的唯一清单。后者按第 5.3 节装配，不能被反射覆盖检查排除。

在已知公共类型上，普通非下划线成员默认进入公共覆盖检查；新增成员缺少说明时失败，
不悄悄跳过。外部框架继承成员、内部实现类不因可被 `dir()` 看见就自动成为公共 API。
`__getitem__` 等实际 DSL 语法按公共协议显式纳入；不全量公开 Python 的特殊方法。

反射使用静态成员描述，不调用 property getter，不构造业务对象，不探测执行成功与否。
确需排除的继承实现细节由拥有者声明并给出局部理由，不能以大范围忽略隐藏覆盖缺口。
属性/字段的名称和类型从已声明字段及 getter 返回注解提取，生成 `attribute` 条目；
其意义、读写权限和效果复用拥有者文档与类型约束，不能从“没有 setter”推断所有读写行为。

| 自动提取 | 来源 |
| --- | --- |
| 参数、顺序、参数种类、默认值、返回注解 | 实际安装 callable 的签名 |
| 固定选项 | 已有 `Literal`、Enum 或拥有者的闭合类型定义 |
| 字段与属性类型、公共方法身份 | 公共类型及其受支持继承关系 |
| 普通成员的 canonical 路径 | 已登记的父语义 target 与公共成员名；特殊命名由拥有者声明 |
| 类型成员目录、出入参类型链接 | 反射结果与真实已登记类型 |
| 导航成员、反向引用、未知 target 候选 | 拥有者声明装配后的同一 registry |

`Any`、字符串描述或运行时动态值不能被当作明确类型；注解不能证明条件返回、实例兼容性或
业务意义。动态返回族需要拥有者补充闭合规则，不靠扫描函数体猜测。

### 5.2 作者声明不可机械推导的事实

| 主动声明 | 为什么需要 |
| --- | --- |
| 目的、适用问题和主要发现归属 | 代码结构不能决定任务导航 |
| 参数意义、获取途径、`None`/省略的含义 | 相同 Python 类型可承担完全不同的角色 |
| 粒度、单位、归属、时间、空值及跨参数约束 | 签名不能表达完整语义 |
| 来源访问、schema 检查、执行、写入等效果 | 是否存在某个函数调用不足以证明公共效果 |
| 返回值代表什么、最小示例及外部前提 | 类型名不能解释用途和正确获取方式 |
| 特殊协议、接收类型变体及无法派生的身份 | 必须由公共契约作出明确承诺 |

主动声明仍只有一份。普通公共 callable 的 docstring 拥有参数和使用语义；registry 绑定
callable、结构化导航及必要的额外元数据。已有语义类型、约束或错误注册表拥有的规则直接
引用，不把它们复制到 docstring、registry 文本和 renderer 三处。

新元数据优先靠近对应拥有者，复用现有原生 descriptor。无需为此引入新的 schema 语言、
文档编译平台或第二份 API 清单。现有重复字段按拥有者逐项收敛，不能用“自动生成”掩盖
不一致，也不能在缺少声明时捏造语义。

### 5.3 原生 Deferred 的表达式语法

`Metric.where`、`Metric.per`、`row.sum` 等是 Marivo 支持的表达式语法，不一定存在同名
Python 类方法。原生 Deferred 可以通过动态属性捕获调用，且同一 Python 类型承载 Field、
Row、Metric、Samples 等不同语义角色。反射 `type(value)` 既不能区分这些角色，也不能证明
某个动态属性受 Marivo 支持。

这些能力沿用 semantic 拥有者的显式语法描述，按统一命名提供 `semantic.metric.where` 等
精确 target；保留的是能力及契约，旧大小写路径在交付时替换：

- 接收角色、操作名、参数名与种类、必填项、默认值、允许的输入角色及输出角色，来自实际
  表达式构造/消费校验所拥有的规则。可复用的规则直接引用；不能复用时用独立准入用例校验
  描述与真实行为一致，不复制一套运行时验证器。
- 语义用途、输入获取、效果、示例和发现归属由同一领域补充。没有 callable 的条目不要求
  伪造 Python binding 或 docstring，页面明确显示为表达式语法及其受支持调用形态。
- `semantic.expressions` 按语义角色导向 `semantic.row`、`semantic.metric`、
  `semantic.metric_samples` 等对象/角色页，再链接精确语法页。对象定义导航与表达式导航
  共用这些页面，不因进入路径不同复制角色说明。
  返回原生 Deferred 的 API 需披露其输出角色和对应组，不能只链接通用 `Deferred` 类型。
- 原生 Deferred 的发现使用这些字符串 target，不要求 `marivo.help(type(value))` 或
  `marivo.help(value.where)` 成功；取动态属性本身可能只产生另一个 Deferred。
- 不枚举任意 Ibis 动态属性，不执行表达式来探测可用方法，不为发现能力引入包装类型。
  缺少输入或尚未确定的角色条件继续由实际消费校验处理。

表达式能力与普通 callable 使用同一 Help 路由、导航和渲染通道。区别只在结构事实的来源：
普通 callable 从实际签名提取，表达式语法从领域支持规则取得。表达式描述必须进入公共
覆盖与可达性检查，不能因 `callable_path` 为空而遗漏。

### 5.4 自动校验，而非自动批准

装配及独立测试检查：公共接口覆盖、签名一致、target 唯一、链接可解析、任务可达性、
示例前提、输出预算和 CLI/Python 等价性。公共导出快照独立于 renderer，防止遗漏被一起
生成后仍然通过。新增公共方法需要满足既有公共文档契约，不能自动补上空白说明后上线。
表达式语法另以实际构造/消费准入行为检查参数、默认值和角色约束；至少覆盖一个合法组合
和一个错误角色或参数的拒绝，避免 descriptor 与 renderer 自我一致但脱离执行契约。

这些检查证明暴露一致，不证明实现正确、数据源支持或业务结论成立。

## 6. 实例展示与 contract 的去留

### 6.1 `.show()` 成为默认实例入口

有独立可解释状态的 Marivo 对象通过 `.show()` 输出有界卡片；repr 给出类型、身份和
`.show()` 提示。普通标量、查找 Ref 和原生 Deferred 不为统一外观强行包装。

| 对象 | 默认披露的实例事实 | 读取边界 |
| --- | --- | --- |
| Query | 输出名与单位、完整坐标、捕获范围、归属、已知/未解析类型及定义身份 | 捕获元数据；不读 schema 或来源，不创建 Run |
| Population | 坐标轴、归属、范围、显式键数量或尚未求值的状态 | 不枚举尚未执行的来源键 |
| Column | 已绑定输入身份、值/坐标角色、名称、已知单位和类型 | 只读已有绑定，不反查来源 |
| Artifact | 已提交身份、范围、结果预览、限制和真实保存记录入口 | 可读取已提交 Store 数据；不加载当前语义或重放来源 |
| Catalog/authoring 结果 | 本次定义、检查结果、遗漏及真实读取入口 | 按其原生结果契约，不隐式补做检查 |

Query 新增 `.show()`；Population 现有 `.show()` 调整为直接输出卡片并返回 `None`；Column
新增独立的绑定事实展示，替换要求读父 Query contract 的 repr 指引。具体显示参数维持各
拥有者的语义，不引入全库统一参数集合或新的公共 Inspector 类。

默认卡片不列全套方法、签名、actions。末尾提供精确类型 Help target；发生内容省略时，
另给出实际可用的展开/读取方式及省略数量。已有公开字段可程序化读取；关键事实若只能藏在
字符串里，应在拥有者的类型化对象上补齐必要字段，不另建通用 facts 字典。

本次明确补齐 `Query.output_names: tuple[str, ...]`，只读返回完整、按定义顺序排列的输出名，
不执行查询。Query 卡片省略输出时指向该属性；agent 无需解析卡片或读取私有 graph 才能
构造 `query[name]`。Population 的完整轴继续读取现有 `.axes`。不为此新增通用 schema DTO。

卡片可以披露一个由当前状态决定的实际读取提示，例如存在保存的 Finding 时给出读取入口。
提示只引用实际接口和记录状态，不独立维护“可调用”清单，不替代 API 校验。

### 6.2 删除通用 action contract

Query、Population 不再暴露 `.contract()`，并移除仅服务该机制的 `AnalysisContract` 和
`AnalysisAction` 公共类型。原卡片中有用的捕获事实移入对象展示或必要的公开字段。
固定调用模板不迁移到另一份 actions/next_actions 清单。

类型页已经可以自动列出 `select`、`where`、`execute` 等公开方法。当前输出名由 Query
展示，agent 将二者组合；无需为了生成 `query.select({'revenue': query['revenue']})`
再维护一套接收对象动作注册。

`.show()` 不承诺 `execute()` 必然成功、不检查未知输入、不证明业务适用性。
涉及其他对象、物理类型或来源能力的条件仍由真实调用的现有校验负责；不得新建一套
“可调用性预测”实现，也不通过试执行所有方法来探测能力。

### 6.3 按独立用途保留领域契约

| 现有机制 | 目标决策 |
| --- | --- |
| Query/Population `AnalysisContract.actions` | 删除；实例展示与静态类型目录接替 |
| `DiscoverySnapshotContract` | 删除重复的展示封装；已有 snapshot 字段、状态和 `.show()` 接替 |
| `ExpressionContract` 与 `catalog.describe(ref)` | 保留结构化定义语义；roots、unit、sum permission 等有独立领域价值 |
| 表达式 `.contract()` | 保留为获取上述类型化语义值的 API；默认阅读用已有 `.show()`，不列为通用发现步骤 |
| `TimeScopeContractV1` 与 `TimeScope.contract()` | 保留现有时间身份/持久化用途；默认阅读用 `TimeScope.show()` |
| 终端 Artifact | 继续无 contract，不恢复计算接收者 |

保留的领域对象不能承接被删除的通用 action 目录。表达式中表示阶段语义的事实继续由原
表达式类型所有者负责；静态方法目录移交 Help。实现时须逐个检查实际消费者，保留时间
序列化、语义描述与持久化行为，不能按方法名做批量替换。

## 7. 结构化错误与效果披露

错误沿用所属 DatasourceError、SemanticError、AnalysisError 层级和共享模板，保留
expected、received、location 及实际可得的修复事实。短错误应足够支持主要修复；只有需要
通用语法或类型知识时才跟随精确 Help target。

例如跨 Query 的 Column 错误指出两个输入身份，并指导从当前 Query 获取 Column；
不能静默替换输入。动态字段候选取自本次真实对象，静态错误类型页不维护另一份候选表。

每个 API 的效果由拥有者明确声明：逻辑构造、metadata/schema 读取、已提交记录读取、
来源数据读取、执行或状态写入。不是每个方法都需要多个阶段标签；只披露会影响 agent
决策的效果和必要条件，复用现有领域术语。

“对象存在”“来源连接成功”“执行路线支持”“本次成功”“业务结论成立”是不同证据。
Help、展示和错误不能相互替代这些证据，也不能自动调整业务口径来修复失败。

## 8. Skill、文档与扩展边界

Packaged skills 保留复用优先、业务意义权责、证据判断、交接和停止条件。移除要求所有逻辑
对象先读 contract 的步骤；按“不知道调用查 Help，不知道实例读 show，失败读错误”指导。
不把参数表、完整 API 配方、方法清单或强制诊断梯度搬进 skill。

English docstrings、Help 与错误继续使用英文。站点最新英文/中文文档按公开入口同步。
本中文设计不要求将产品 Help 本地化。

Ontology 复用相同静态 Help 通道，保持非执行上下文的独立拥有者，不新增顶层工作流根。
本设计不扩大其运行时能力。

不增加 agent 专用业务 DSL、MCP 执行层、全局 session 别名、对象跨进程传输协议或从
Artifact 回到计算的通道。首次调用门槛降低不应以隐藏执行上下文或绕过语义为代价。

## 9. Agent 使用方式

以下为已实施的公开流程，包括 focused CLI Help 和 Query/Column `.show()`。
这些是按需分支，不是每个任务必须顺序完成的检查表。

### 9.1 新分析，未知 API

```sh
.venv/bin/python -m marivo help analysis
# Follow the task route to the exact API needed next.
.venv/bin/python -m marivo help analysis.session.observe
```

agent 在 Python 脚本中使用该叶子说明的显式 Session、受治理输入与时间范围。
缺少具体业务输入时读取 Catalog；缺少可复用业务意义时进入语义 authoring。
不要为了查 API 加载项目，也不要为了获得业务定义只读静态 Help。

### 9.2 已有 Query，需要继续计算

```python
import marivo

# query already exists in this Python execution context.
query.show()
marivo.help(query.select)
selected = query.select({"revenue": query["revenue"]})
artifact = selected.execute()
artifact.show()
```

示例假设 `query.show()` 确认已有 `revenue` 输出。若 agent 已知 `select` 的契约，无需重复
查 Help；若需要寻找方法，可直接读 `marivo.help(type(query))`。不经过根导航或 contract。

独立 shell 调用不共享 Python 变量。agent 应在原脚本/持久执行上下文中使用对象，或通过
现有公开 Session/Artifact 入口恢复已提交工作；Help CLI 不恢复临时 Query。

### 9.3 已有 Artifact 或失败

已有 Artifact 直接 `.show()`；需要完整导出或保存的 Evidence 时，只查询对应精确方法。
需要新鲜计算则从受治理输入构造 Query；已保存输出不能重入 typed computation。

失败时先修复错误指明的局部输入或状态。只有确实缺少 API 规则时再读其 Help target。
环境不确定才运行 doctor；不能把每次语义错误都变成完整环境检查。

### 9.4 查询属性契约与读取值

Query 卡片省略输出时，agent 可以沿属性 target 查询完整读取方式：

```sh
.venv/bin/python -m marivo help analysis.query.output_names
```

在持有 Query 的 Python 上下文中，同一查询与实际读取分别写为：

```python
import marivo

# query already exists; omit the Help read if its contract is already known.
marivo.help("analysis.query.output_names")
names = query.output_names
```

属性查询规则同样适用于公开字段，例如 Population 的 `.axes`。不要求 agent 判断它在
Python 内部由 dataclass 字段还是 property 实现。

### 9.5 原生 Deferred 的能力发现

已通过 Catalog 或完成的 finalize/resolve 流程取得受治理的 Metric 时，返回契约应将 agent
导向 Metric 语法组。若还不知道表达式类别，可从 expressions 根按问题选择：

```sh
.venv/bin/python -m marivo help semantic.expressions
.venv/bin/python -m marivo help semantic.metric
.venv/bin/python -m marivo help semantic.metric.where
```

在已有执行上下文中使用该语法：

```python
import marivo

# revenue is a resolved Metric; status is a compatible governed Field.
# The business question explicitly restricts contributions to paid orders.
marivo.help("semantic.metric.where")
paid_revenue = revenue.where(status == "paid")
```

这里 `revenue` 和 `status` 是已获取的原生 Deferred 输入，叶子说明二者的角色与兼容约束。
agent 不通过 Deferred 类的方法目录判断支持范围。`paid_revenue` 仍是逻辑表达式；将其交给
`session.observe` 才构造 Query，执行另由 `Query.execute()` 请求。已读过 CLI 叶子时无需
重复 Python Help；上述两种读取方式展示同一契约。

## 10. 最小实现结构

复用现有 `marivo._help` coordinator、各领域 `_capabilities`、中立反射工具及原生 renderer：

```text
Public exports / namespaces / owned return types ─┐
Installed signatures / annotations ───────────────┼→ native owner registry
Owned docs / expression grammar / task groups ────┘       │
                                                       ├→ shared static Help rendering
                                                       │      ├→ CLI
                                                       │      └→ Python
Captured definitions / committed records ────────────────→ object show / typed fields
Real validation failure ────────────────────────────────→ structured error
```

registry 装配身份、语义和链接，不拥有实例生命周期；renderer 只展示，不藏成员清单。
领域拥有者决定元数据，不让 CLI 扫描整个包后自行推断 API。CLI 是薄适配，不重复生成
签名、执行调用或管理项目。

实例展示直接消费对象已有事实，可复用私有纯格式化函数；不为统一外观引入新公共基类、
统一状态机或通用 continuation schema。

## 11. 迁移与交付边界

这是一次公开披露契约变更。开发可以分步验证，完整对外切换必须在同一交付中完成：

| 当前 | 目标及需同步的引用者 |
| --- | --- |
| CLI bootstrap-only，并拒绝 target | CLI 直接访问同一静态 Help；删除拒绝分支及 bootstrap-only 文案/测试 |
| 混合大小写、Python 路径、裸短名和归一化路由 | 小写语义 target 与正式根；同步代码绑定、错误、示例和所有出站链接 |
| `semantic.objects.metric` / `semantic.metric` / `semantic.Metric.where` | 分别迁至 `semantic.metric` / `semantic.metric.define` / `semantic.metric.where`；旧路径含义不能原样沿用 |
| `semantic.expressions.Metric` 等角色页 | 合并到 `semantic.metric`、`semantic.metric_samples`、`semantic.row` 等唯一对象/角色页 |
| `help(result/ref/entry/error)` 实例 briefing | 原生展示/Catalog/错误字段；Help 仅处理静态目标 |
| Query/Population `.contract().show()` | 对象 `.show()` 与完整 `Query.output_names`；同步 repr、Column 指引、skill、示例、类型导出与快照 |
| `AnalysisContract`、`AnalysisAction` | 删除专用实现、注册、公开导出与引用；有用事实移入拥有者 |
| `DiscoverySnapshot.contract()` | snapshot 现有展示和公开字段；删除重复结果类型与 Help |
| 手写成员/签名重复清单 | 在确定公共范围内自动派生；语义与导航显式声明 |
| 仅在类型页中列出的公开字段/property | 生成独立 `attribute` target，补齐读取契约并从类型页链接 |
| 无普通 callable 的 Deferred 语法描述 | 保留领域声明，连接真实准入规则及语义角色导航，不纳入纯反射删除范围 |
| 多处复制用途、参数和约束 | 向现有拥有者收敛，renderer 只消费并校验 |

不保留双 Help 内容、旧 action registry、兼容 alias 或长期双读。持久化的领域契约和
已提交 Artifact 不属于本次删除对象；不得仅为了披露切换升级 Store 或重放来源。

实施前生成逐项的旧 target → 新 target → Python binding 映射并检查冲突，特别标识
`semantic.metric` 这种字符串不变而页面含义变化的项。更新全部出站链接和调用者后再切换；
旧地址可在迁移说明中出现，但不能继续作为可解析别名。映射用于本次迁移核对，不成为
长期运行时的第二份 registry。

建议实施顺序：

1. 固定公共范围与完整 target，收敛结构反射和语义所有者，保留独立覆盖校验。
2. 接通 CLI/Python 共用静态 Help，补齐类型成员发现和局部输入获取路径。
3. 将实例事实移入直接展示/必要公开字段，删除重复 action contract 和实例 Help briefing。
4. 在同一交付中对齐所有消费者、文档、错误和暴露测试，完成完整旅程验证。

需对齐的现行拥有者包括 `AGENTS.md` 的 Agent-Facing Disclosure、公共暴露规范、语义
loading/introspection 文档、分析设计、两份 packaged skills、最新 EN/ZH 站点与示例。
旧 Dataset/Cell 暴露残留按当前 Query/Population/Artifact 契约修正，不作为目标设计依据。

**授权边界：** 设计阶段只新增本文；实施阶段用户已明确授权同步修改 `AGENTS.md` 和两份
packaged skills，并已完成对齐。该授权仅属于本次切换；一般设计或实现请求不自动包含
对这些受保护文件的修改授权。

## 12. 可验证的成功标准

### 12.1 确定性契约验证

- 相同完整 target 的 CLI/Python 静态正文等价，版本与环境身份如实披露；错误退出码正确。
- 所有 target 的片段满足小写 `snake_case`；页面 `Target`、`Kind` 与 registry 一致。
  真实 Python 写法保持原样；`marivo.help(ms.metric)` 解析到定义页，`marivo.help(mv.Query)`
  解析到对象页，绑定方法与其字符串 target 等价，不能因归一化产生碰撞或错误归属。
- 用独立的公共导出、namespace、返回类型与表达式支持规则检查覆盖；新增/删除能力不会静默漂移。
- 任务导航可达，类型成员完整，所有出站 target 可解析；短名不能意外命中其他领域。
- 属性与字段使用 `Kind: attribute`；类型页链接和独立字符串查询均指向同一读取契约。
  覆盖 `Query.output_names`、`Population.axes`、`Artifact.id`；页面不出现伪造的方法调用，
  不以普通 tuple/str 的类型页替代成员说明。CLI/Python 查询结果等价。
- 类型页每个方法都输出真实 canonical target；不能依据 receiver 和方法名推测路径。
  例如 SemanticCatalog 的 preview 导航直接链接到 `semantic.preview`。
- 变体属性使用公共 receiver，并披露适用的 kind/判别条件；例如 Grain.calendar 仅属于
  semantic Grain，不能使 `mv.grain("day")` 看起来也具有该字段。
- 无 Python callable 的原生语法保留 `Kind: expression` 和明确调用语法；覆盖
  `semantic.metric.where`、`semantic.row.sum` 与 `semantic.field.via`。
- 错误候选与命名页面的 Target 均能直接往返查询；覆盖跨领域候选，以及通过类对象查询的
  `ArtifactNotFoundError`、`SessionNotFoundError`。未独立登记的内部异常子类使用最近的
  已登记错误祖先页（如 `analysis.analysis_error`），不生成不存在的子类地址。跨领域
  属性候选保留完整成员路径，避免丢失属性后缀或 class/string 注册范围不一致。
- 用一个读取即报错的 getter 验证属性发现和 Help 渲染不执行它；普通字段也无需构造实例
  或读取值才能生成帮助。实际属性读取的效果与只读性按所属对象契约另行验证。
- 普通 API 签名、默认值与字段类型来自实际安装代码；表达式调用形态与默认值对应领域
  准入规则。约束、效果和输入获取说明没有平行所有者。
- `Metric.where`、`Metric.per`、`row.sum` 等无普通 callable 的语法可从角色导航到达，
  参数/角色的合法与拒绝用例符合实际消费行为；不向 agent 暴露任意 Ibis 方法。
- Help 与逻辑对象展示不加载项目、不读取 schema/来源、不创建 Run；Artifact 只读已提交数据。
  静态成员发现不执行 property getter。
- Query 展示保留输出名、坐标、范围、归属和未解析信息；边界未知时明确说明。
  `output_names` 返回全部名称，卡片发生省略时仍可无来源读取并构造对应 Column。
- terminal Artifact 无 contract、无计算续接；TimeScope 序列化和语义定义描述保持原有行为。
- 覆盖至少一个跨 Query Column 错误，以及“方法存在但来源执行未获支持”的反例。
  它们不能被展示或 Help 错误地宣称已获准执行。
- 删除项没有残留路由、公开导出、skill 指令或示例；复制示例的真实输入前提可验证。
  当前规范、API 文档与最新中英文站点中的 Query/Population/DiscoverySnapshot 展示示例
  必须使用现有公开成员；历史 release notes 与有效 TimeScope/Expression contract 不作全局替换。

### 12.2 Agent 成本与使用验证

使用相同问题、数据、安装环境和初始知识比较现状与目标态，至少覆盖：

1. 新项目中的 API 发现与一次受治理计算。
2. 找到并解释已有业务定义，不额外查询来源。
3. 已有 Query 的输出选择与后续执行。
4. Artifact 阅读、保存 Evidence 与无来源冷恢复。
5. 缺失输入/跨 Query 错误的局部修复。
6. 从已知物理数据和已明确的业务定义开始，完成 datasource 与 semantic authoring，再交给 Query。

第 6 条使用一个本地、已支持的数据源和最小业务模型，不增加后端矩阵。agent 经公开 Help
发现 datasource 声明和物理结构检查，读取实际检查结果；声明所需语义依赖，完成
capture → finalize → resolve 和表达式组合，在项目中加载并验证最小语义切片，最终取得
Session 可消费的输入并执行一个 Query。使用两侧相同的数据与独立预期结果核对交付。

旅程包含一次明确的声明错误（例如引用 kind 不匹配），要求依据结构化错误完成局部修复。
记录 Ref 如何取得/解析、检查结果如何直接读取、原生 Deferred 如何通过角色 Help 继续组合，
验证移除 Ref/CatalogEntry briefing 和 DiscoverySnapshot contract 后仍有完整的公开路径。
构造、项目验证与来源执行分别记录；不能将完成声明或连接成功计为 Query 执行通过。

记录首次有效调用前的阅读轮次、完整披露字符数（含重复读取）、无效 target、API 猜测、
失败与修复次数、任务结果和耗时。保留模型原始轨迹，不将控制器脚本冒充自主 agent。
实例/环境恢复困难也计入结果，不从样本中删去失败。

现有 [context budget](../../testing/analysis-help-context.md) 是可复用的字符/页数代理，
不等于模型 token 或使用效率。保留原基线证据；因移除 contract 需要调整脚本时，保持
业务目标与必需信息相同，不能通过少读必要事实或改写基线制造收益。

成功必须同时满足：主要旅程完成度和语义正确性不下降；重复发现/无效跳转减少；目标对象
不再要求 contract 前置步骤；未引入新的执行、身份或恢复协议。实测若增加 API 猜测或
修复成本，应调整类型目录与展示的连接，而不是先增加另一套推荐/actions 体系。
具体量化门槛在实施基线采集后固定，不把现有单项字数下降率当成总体认知成本证明。

### 12.3 工程证据边界

文档阶段只验证路径、引用、现状描述和设计一致性。实施阶段先跑受影响的 Help、CLI、
类型、实例展示与错误测试；修改 Python 完成 typing，公共共享行为完成未缩小范围的
`make check-agent`，站点 EN/ZH 完成相应内容验证。

真实 agent 旅程使用同一冻结安装产物验证环境归属与可用性；静态检查、本地数据验算、
自主 agent、相关远端 Runtime 和 release/install 接受分别报告。普通实现不运行完整 release
acceptance，也不因静态 Help 改动启动无关外部服务。

## 13. 已作出的取舍

- 选择 CLI 直接发现，承担它与 Python 共用路由和输出的测试成本，减少 shell 引导跳转。
- 选择实例事实加自动方法目录，承担 agent 组合参数的少量工作，删除通用 action 体系的
  学习与维护成本；优先保证名称、输入身份和获取路径清楚。
- 保留任务导航的人工判断，因为 Python 类结构不等于业务任务结构。
- 保留领域契约，但将其从默认发现流程中移开，避免“每个对象都要 contract”的规则。
- 不承诺帮助系统证明当前调用成功；真实类型与运行时校验继续拥有最终判断。

这些决策不依赖未来能力、兼容层或新的基础设施。若实测不能降低完整任务成本，应依据
失败轨迹修改具体信息连接，而不是以页面缩短或接口数量减少宣称成功。


## 14. 实施与验证记录

本次完成 CLI/Python 静态 Help、严格语义 target、属性页面、Deferred 角色导航，以及
Query/Population/Column 实例展示。删除通用 AnalysisContract/AnalysisAction 与 snapshot
展示 contract；语义定义和时间身份契约保留。仓库规则、两份 skills、API 文档、当前规范
及最新中英文站点同步更新。未创建 commit 或发布。

整合过程中记录并修复了类型/构造器地址冲突、角色页遗漏、超长逻辑事实展示、冻结字段
可写性误报、动态执行错误中的旧 target，以及默认根页地址不可查询的问题。
测试迁移保留真实结果、错误详情和 credential canary 检查；原始 context budget 基线未改写。

验证包括完整 `make check-agent`（lint、导入边界、typing、日常测试、API 文档）、
原始上下文预算 slow 用例、310 个公共导航页面的递归可达性检查，以及站点内容和完整构建。
最终完整门禁为 3944 passed，typing 检查 297 个源文件通过，lint/导入边界与 API 文档通过。
原始 context budget slow 用例为 1 passed；站点内容验证及完整构建通过，验证 44 个最新
使用页面与 343 个内部路由/锚点。最终门禁日志为 `/tmp/marivo-disclosure-final-gate3.log`；
不把中间失败的整合运行算作通过。

一位不读取私有实现或现有测试的独立 agent 仅依靠公开 Help，在临时项目中从三行 CSV
完成 datasource/语义定义、一次非法 Metric 声明的修复、Query 展示/选择和执行。
独立预期为 E=30 CNY、W=30 CNY，结果精确匹配。来源文件和整个模型目录移走后，
新进程仍可恢复相同 Session/Artifact、读取三条保存 Evidence 并完整导出相同结果。
四个属性页的 CLI/Python 正文完全相同；Query/Population/Artifact 均无通用 contract。

该 agent 保留了最初失败和修复后的轨迹：脚本环境误导入旧安装包、相对 CSV 路径依赖 cwd、
执行错误提示旧大写 target，以及对 pandas MultiIndex 的错误假设。其中旧错误 target 是
产品缺陷，已修复并由该 agent 独立复验；其余记录为环境或使用过程中的事实，未改写为成功。
完整脚本、报告及原始日志保存在 `/tmp/marivo-disclosure-acceptance/`。

这些是显式指定源码包路径的本地验证，未证明 wheel 安装、远端 Runtime 或 release 接受；
本次没有运行完整 release-check，也没有为静态披露变更启动外部服务。

### 14.1 审查修复

后续只读审查发现并修复六项 P2：类型页缺少实际方法地址、变体属性缺少适用条件、
原生 Deferred 被标成 topic、跨领域错误候选使用旧大小写、错误子类页头不可查询，
以及当前文档遗漏已删除的 contract 调用。方法归属和变体条件由原生拥有者明确声明，
未知方法不能静默回退；私有分析错误子类复用最近的已登记错误契约。

新增回归覆盖真实失败类型的页头往返、CLI 错误建议跟随、完整属性候选地址、
运行时字段存在性与 getter 不执行、表达式 Kind/Syntax、方法导航和文档中的真实成员调用。
最新中英文使用文档及 API 文档同步迁移；历史发布记录和独立领域 contract 保留。

修复冻结后由独立 agent 只读复审，未发现新的 P2 及以上问题。独立聚焦回归为
103 passed；1002 个页面完成页头地址往返，并跟随 902 条 Help 链接，无解析失败。

修复后的完整 `make check-agent` 通过：3969 passed，297 个源码文件 typing 通过，
格式、lint、导入边界和 API 文档通过。原始上下文预算 slow 用例为 1 passed，基线未改写。
站点内容验证覆盖 381 个文件，完整构建通过，并验证 44 个最新使用页面及 343 个内部链接。
本轮日志为 `/tmp/marivo-p2-final-gate.log`、`/tmp/marivo-p2-context.log` 和
`/tmp/marivo-p2-site-build.log`。验证仍限于当前源码的本地环境，未提交、未发布，
未运行远程 Runtime 或 release 验收。
