# Marivo Python 语义层总体设计

状态：draft design。本文描述 `marivo.semantic` 作为 Marivo Python 库语义层的目标态设计、当前 v1 边界和 agent 使用契约。它是设计侧文档，不表示所有目标态能力都已经实现。

本文面向 Claude Code、Codex 等通用 coding agent。设计目标不是让 agent 记住一套私有 DSL，而是让 agent 能像维护普通 Python 项目一样维护业务语义：读取现有对象、声明明确模型、用 Ibis 表达计算口径、保留 SQL 来源、运行校验，并把稳定 semantic refs 交给 `marivo.analysis` 消费。

## 设计目标

`marivo.semantic` 是 Python-native 分析链路的业务对象契约。它回答的是“这个分析项目里有哪些可被稳定引用的业务对象”，而不是“如何把 YAML、SQL 或运行时 API 包成另一个入口”。

目标态满足以下要求：

- Python 文件是语义定义的 source of truth。agent 修改业务口径时应改 Python authoring 文件，而不是编辑生成物或运行时存储。
- Datasource 是项目级可分享配置，定义在 `models/datasources/*.py`；semantic domain 只通过全局 datasource name 引用它。
- 语义对象必须可被通用 agent 静态阅读：entity、dimension、time_dimension、metric、relationship、decomposition 和 provenance 都有显式 Python 声明。
- 业务口径不能靠字段名、表名或自然语言自动猜测。agent 必须通过 decorated refs、函数签名、`provenance=ms.from_sql(...)`、parity result 和结构化错误来收敛。
- 归属、依赖和项目边界必须来自显式声明或显式 default domain。domain 不能由文件路径猜测，metric 不能由函数参数名推断 entity，reader 不能靠 thread-local active project 隐式选项目。
- Ibis 是 Python 语义层唯一表达计算口径的执行表达式层。SQL 可以作为 provenance 和 parity oracle 保留，但不作为主要 authoring 语言。
- `analysis`、后续 operator、skill 或脚本只消费稳定 semantic refs 和 materialized Ibis 表达式，不直接依赖用户项目内的 Python 文件布局细节。
- 失败语义 fail closed。装饰、加载、组装、物化、parity 任一阶段无法证明契约成立时，应给出结构化错误，而不是降级为 best-effort 猜测。

核心判断标准是：如果一个业务对象会被下游分析引用，它必须先进入语义层；如果一个规则只存在于 agent 的临时提示词或 SQL 草稿里，它还不是稳定语义。

## Authoring 快速路径

The stepwise authoring workflow for agents is defined in
`docs/specs/semantic/stepwise-authoring-design.md`. The current public flow is
`help -> discover -> settle/grill -> author -> verify`, followed by readiness
closeout before analysis handoff. It replaces the earlier three-phase
authoring pipeline.

Authoring guidance is split across three layers, each with one job:

- **`ms.help("<constructor-or-object>")` — static authoring contract.** The
  constructor, required and optional parameters, allowed values, defaults, omit
  rules, nested parse shapes, and static constraints for every semantic-layer
  object. Help tells the agent what parameters must be settled; it carries no
  runtime data and no fixed parameter-value source labels.
- **`md.discover_*` — runtime datasource evidence.** Bounded, evidence-only
  `DatasourceResult` objects whose public contract is `.show()` / `.render()`.
  Discovery supplies the physical facts an agent needs to settle constructor
  values, but concrete result classes and internal evidence fields are not
  public authoring inputs. Discovery does not author objects, infer business
  meaning, or carry judgment targets.
- **`ms.verify_object(...)` / `ms.readiness(...)` / load errors — validation.**
  Blockers, registry/project state, object validity, and readiness are exposed
  after authoring through verification, readiness checks, and load failures.

Before authoring a datasource-backed semantic object, agents collect bounded
datasource evidence with `md.discover_entity(...)`,
`md.discover_dimensions(...)`, `md.discover_time_dimensions(...)`,
`md.discover_measures(...)`, or `md.discover_relationship(...)`, read the
bounded `.show()` output, then author one semantic object and run
`ms.verify_object(...)`. Discovery does not author objects or infer business
meaning.

目标态标准 stepwise authoring workflow 使用每个 domain 一个
`models/semantic/<model>/_domain.py` 文件。agent 应在
`models/semantic/sales/_domain.py` 中完成从 entity 到 metric 的声明；项目
datasource 单独放在 `models/datasources/warehouse.py`。底层 loader 仍可执行
同目录 sibling `.py` 文件，但这是更低层能力，不是当前正常 agent-authored
文件组织建议。

```python
# models/datasources/warehouse.py
import marivo.datasource as md

md.duckdb(
    name="warehouse",
    path="/data/warehouse.duckdb",
)
```

```python
# models/semantic/sales/_domain.py
import marivo.datasource as md
import marivo.semantic as ms

ms.domain(
    name="sales",
    owner="Mina Zhang",
    ai_context=ms.ai_context(business_definition="Sales analytics"),
)
warehouse = md.ref("datasource.warehouse")

orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
    primary_key=["order_id"],
    ai_context=ms.ai_context(
        business_definition="One row per order before metric-level filters.",
        guardrails=["Do not treat this as paid orders only."],
    ),
)

is_paid = ms.dimension_column(
    name="is_paid",
    entity=orders,
    column="pay_status",
    ai_context=ms.ai_context(
        business_definition="Whether the order is paid.",
    ),
)

@ms.metric(
    entities=[orders],
    additivity="additive",
    provenance=ms.from_sql(
        sql="select sum(amount) as value from orders where pay_status = 1",
        dialect="duckdb",
    ),
    ai_context=ms.ai_context(
        business_definition="Total order amount for paid orders only.",
        guardrails=["Excludes unpaid orders.", "Does not net out refunds."],
        synonyms=["gmv", "paid sales"],
        examples=["What was paid revenue last week?"],
    ),
)
def revenue(order_rows):
    return order_rows.filter(is_paid(order_rows)).amount.sum()
```

这一层的输出不是 pandas DataFrame 或 SQL 字符串，而是可加载的 Python 定义。

### 通用 Authoring 规则

- `name=` 给出时是唯一 semantic identity。
- `name=` 省略时，Python 变量名或函数名作为 fallback identity。
- Python 符号名只是 local alias，不参与 semantic id。
- 语义对象的人类可读文本统一通过 `ai_context.business_definition` 表达，不接受独立的 `description=` 参数。`ai_context.business_definition` 是完整业务定义，可多行，agent 用它判断对象是否匹配用户意图。
- `ai_context` 通过 `ms.ai_context(...)` 构造，不接受原始 dict。schema 适用于 domain、project datasource、entity、dimension、time_dimension、metric 和 relationship 所有对象。所有字段可选，缺失时 `describe` 返回 `null` 或空列表。
- `ms.ai_context(...)` 固定参数是 `business_definition: str | None`、`guardrails: Sequence[str]`、`synonyms: Sequence[str]`、`examples: Sequence[str]`、`instructions: str | None`、`owner_notes: str | None`。非法参数名由 Python TypeError 捕获，值类型错误由 `SemanticDecoratorError` 捕获并包含调用位置信息。
- `business_definition` 和 `guardrails` 对 entity 与 metric 最重要；跨 domain 引用前，agent 应优先读取这两个字段判断是否可复用。
- `examples` 只放自然语言示例问法，不放 SQL、Ibis snippet 或 expected values。
- 未知 `ai_context` 字段 fail closed（由 `ms.ai_context()` 的关键字参数机制保证），避免 agent 把不可消费内容塞进语义契约。

## Registry / Loader

`SemanticProject` 指向一个语义项目根目录。loader 执行受信任的本地 Python 文件，把 decorators 的副作用组装成内存 registry：

```text
semantic/
  sales/
    _domain.py          # agent authoring pipeline keeps all declarations here
  marketing/
    _domain.py
    _exports.py
```

`docs/specs/semantic/stepwise-authoring-design.md` 定义的 stepwise
authoring workflow 只使用每个 domain 的 `_domain.py` 单文件。Loader 仍可以
执行同目录 sibling `.py` 文件，但那是底层 loader 能力，不是当前标准
authoring pipeline 的组织建议。

目标态 loader 规则是：

- 每个 model 必须在 `<root>/<model>/_domain.py` 中调用一次 `ms.domain(name="<model>", owner="<person>", ...)`。`owner` 必填，使用负责该 domain 语义正确性与质量的人名，例如 `"Mina Zhang"`。
- `_domain.py` 是该 domain 的 entrypoint，可以只声明 domain metadata，也可以承载 single-file 快速路径中的 datasource、entity、dimension、metric 和 relationship；但不能声明多个 domain，也不能用与目录名不同的 `name`。
- `ms.domain(default=...)` 缺省为 `True`。默认场景下，同目录 sibling files 里的对象可以省略重复 `domain=`（`DomainRef`）；如果项目希望 review 时强制每个对象显式写 `domain=`，可在 `_domain.py` 里传 `default=False`。
- default domain 作用域仅限当前 domain 目录的顶层 sibling files，不向子目录传播。`sales/subdomain/*.py` 不继承 `sales/_domain.py` 的 default；子目录若要被加载，应作为独立 domain 域或由项目明确扩展 loader 规则。
- default domain 是 loader 在加载该 domain 目录时的上下文，不随 `from x import *` 或普通 Python import 跨 module boundary 传播。decorator 在 loader context 外执行仍然 fail closed。
- 显式 `domain=other_ref` 永远覆盖 default，并触发组织校验；对象不会因为文件移动而静默改名。
- 文件系统路径只用于发现候选 Python 文件和做组织校验；对象身份只来自显式 `domain=`（`DomainRef`）或显式 default domain。
- loader 采用 two-pass 语义：第一阶段 collect 所有声明，第二阶段 resolve refs 和校验依赖。文件名和 sibling sort order 不应影响合法模型是否能加载。
- Python 文件是受信任本地代码，不做 sandbox。
- 成功加载后 registry 进入 `ready`；失败时清空部分模型，进入 `errored`，并记录结构化 `load_errors`。

文件组织应优先服务 agent 的增量修改。当前标准 stepwise authoring workflow 选择
把一个 domain 的声明集中在 `_domain.py`，按依赖顺序维护 entity、dimension、time
dimension、metric、relationship 和 derived metric。底层 loader 支持 sibling files，
但多文件 authoring 需要单独说明 import order、default domain scope 和 review
边界，不能作为默认 agent 工作流。

For agent-authored models, the normal authoring contract is one file:

```text
models/semantic/<model>/
  _domain.py
```

The loader may still execute sibling Python files as a lower-level capability, but the stepwise authoring workflow in `stepwise-authoring-design.md` uses `_domain.py` as the single normal authoring file.

## Reader / Introspection

Reader 层让 agent 和 `analysis` 通过 `SemanticCatalog` 读取明确的语义对象，而不是重新解析文件或依赖进程全局状态：

```python
import marivo.semantic as ms

catalog = ms.load()
catalog.list().show()
sales = catalog.get("domain.sales")
catalog.list(sales.ref, kind=ms.SemanticKind.METRIC).show()
catalog.list(kind=ms.SemanticKind.METRIC).show()  # all metrics across every domain
revenue = catalog.get("metric.sales.revenue")
revenue.details().show()                       # bounded details card
revenue.children                               # child SemanticRef subclass values (each has .id and .kind)
```

目标态 reader / introspection surface 以 catalog methods 为主：

| API | 语义 |
| --- | --- |
| `ms.load(workspace_dir=None)` | 加载当前项目并返回 `SemanticCatalog` |
| `catalog.get("<kind>.<semantic_id>")` | 用 typed id 解析并验证单个 `SemanticObject` |
| `catalog.list(scope=None, kind=None)` | 用 `SemanticRef` scope 浏览 domain、datasource、entity、dimension、time_dimension、metric、relationship；顶层 `kind=SemanticKind.METRIC` 跨域搜索 |
| `catalog.preview(ref, limit=..., context_columns=None)` | 对 `SemanticRef` 指向的 entity / dimension / time_dimension / measure / metric 做有界预览 |
| `catalog.readiness(refs=None)` | 对 handoff `SemanticRef` 做结构 readiness gate |
| `project.load()` | 重新加载该项目 |
| `project.richness(demand=None)` | 返回纯 advisory 的 demand-ranked semantic coverage/depth gap report，不阻塞 readiness |

`project.richness(...)` returns a `RichnessReport`; callers seed ranking with
`DemandSignal`. `DemandSignal`, `RichnessGap`, and `RichnessReport` are public
`marivo.semantic` exports.

`ms.help(symbol=None)` 是模块级帮助 helper，独立于
`SemanticProject` 实例使用，不需要 active project。它是 authoring guidance 三层
模型中的静态契约层：对每个 semantic-layer 对象构造器/主题，输出构造器、必填/可选参数、
允许值、默认值、省略规则、嵌套 parse 形态和静态约束。`ms.help()` 打印帮助文本并返回 None。
`ms.help("constraints")` 是 authoring / validation
约束目录的统一入口，不另设并行 helper。Help 只描述参数必须满足什么，不携带运行时数据，
也不固定参数取值来源标签——运行时证据由 `md.discover_*` 提供。

`find_project()` 的 project 判定只要求 `models/semantic/` 目录存在。空目录也算语义项目：`SemanticProject` 可返回，load 后 registry 为 `ready`，`catalog.list().objects` 返回空 tuple。如果 `models/semantic/` 存在但不是目录，必须 fail closed。

Use `catalog.get("<kind>.<semantic_id>")` to resolve a known object, then pass `SemanticObject.ref` into `catalog.list(...)`, `catalog.preview(...)`, and `catalog.readiness(...)`. The catalog surface is deterministic and does not depend on fuzzy or embedding-based recall. At the top level (no scope), `kind=SemanticKind.METRIC` searches across all domains; `catalog.list(domain.ref, kind=...)` scopes to a single domain.

`catalog.list(...)` 和 `catalog.get(...)` 不写 stdout。需要人类可读输出时显式调用 `.show()`；程序化消费使用 `SemanticObject.ref`、`.details()`、`.children` 和 `SemanticObjectList.objects`。`details()` 返回的结构化 dataclass 也支持 `.render()` / `.show()`，用于 agent-facing bounded card 输出。

`catalog.get("<kind>.<semantic_id>").details()` 返回结构化 dataclass，而不是只打印文本。所有 details 对象直接暴露 `ref`、`kind`、`name`、`domain`、`context`、`business_definition`、`guardrails`、`synonyms`、`examples`、`instructions`、`owner_notes`、`python_symbol`、`source_location`、`parents`、`children` 和 `dependents`。各类型还暴露自己的公共语义事实：datasource 的 `backend_type`、literal `fields` 和 env var 名称 `env_refs`；domain 的 `default`；entity 的 `datasource`、`source`、`primary_key` 和 `versioning`；measure 的 `additivity` 和 `unit`；time dimension 的 parse/granularity/timezone/sample interval；metric 的 entity/composition/additivity/provenance/parity/unit；relationship 的 join keys。`details().show()` 输出同一信息的有界可读卡片；secret 只允许以 env var 名称出现，不能输出解析后的 secret value。

free function 形态只允许作为 REPL 糖保留；如果没有显式 active project，必须 fail closed，不能 silent fallback 到 CWD 推断。

## Result Contract

All semantic project methods that return result objects do not write stdout.
Inspection is explicit:

- `result.show()` — print a bounded result card and return None
- `result.render()` — return the same bounded text without writing stdout
- `repr(result)` — one-line cold-start hint pointing to `.show()`

Catalog browsing returns `SemanticObjectList`, not raw lists. Use `.objects`,
`.refs()`, `.render()`, and `.show()` for common agent access patterns.

## Materialization

Materialization 层把已注册的 Python 函数重新组合成 Ibis 对象。它是 semantic 内部和 analysis runtime 的实现细节；agent-facing 读取和预览使用 `SemanticCatalog`：

```python
import marivo.semantic as ms

catalog = ms.load()
revenue = catalog.get("metric.sales.revenue")
preview = catalog.preview(revenue.ref)
preview.show()
```

目标态上，materialization 不作为 `SemanticProject` 公共 API 暴露。需要表达式的 semantic internals 使用 resolver / private registry primitives；analysis 通过 session-owned catalog 和 datasource connection runtime 执行。

`describe(..., compile_sql=True)` 应能在不执行查询的情况下返回 Ibis repr、backend-compiled SQL、`provenance` SQL 和 parity status，帮助 agent 调试口径差异。编译契约：

- compile target 默认来自 metric 依赖 datasource 的 `backend_type`。
- 系统通过内部 datasource 连接服务获取 backend；实际 backend dialect 必须与声明的 `backend_type` 一致，否则 fail closed。
- 无可用 backend 连接时，系统应使用 `backend_type` 对应的 dry compiler；若该 backend type 没有可用 compiler，返回结构化 `compile_error`，而不是执行查询。
- 多 datasource metric 在 compile 和 parity 中默认 fail closed；后续 federation 需要单独设计。
- 编译失败返回 `compiled_sql=null`、`compile_error={kind,message,refs}`；`strict=True` 时可 raise。

## 核心对象模型

### SemanticProject

目标态：`SemanticProject` 是唯一显式项目边界；reader、materialization 都优先通过 project methods 调用。

```python
from marivo.semantic import SemanticProject

project = SemanticProject(root="/path/to/marivo/semantic")
```

它拥有独立 registry 和加载锁。目标态上，一个 `analysis` session 应显式绑定到项目 root 下的语义项目，避免在不同 CWD 或不同 checkout 间误读模型。

### Domain

domain 是业务域边界，例如 `sales`、`marketing`、`subscription`。domain 名称参与下游 semantic id，例如 `sales.revenue`。agent 不应用自然语言近似匹配替代 domain id；如果不确定，应先 `catalog.list()` / `catalog.get(...)`。

当前标准 authoring pipeline 不要求 `_exports.py`。跨 domain 或前向引用无法自然使用
decorated Python ref 时，使用显式 typed fallback ref，例如
`ms.ref("metric.marketing.sessions")`、`ms.ref("entity.sales.orders")` 或
`ms.ref("dimension.sales.orders.user_id")`。裸 semantic-id 字符串不能作为对象间
authoring 参数引用。已有项目若维护 `_exports.py`，它属于多文件 loader 工作流的边界文件，
不是本管线的默认组织要求。

### Datasource

Datasource 是项目级配置，不属于任何 semantic domain。它定义在 `models/datasources/*.py`，可随 `models/semantic/` 一起复制到其他分析项目复用。

```python
import marivo.datasource as md

md.trino(
    name="warehouse",
    host="trino.example.com",
    port=8080,
    catalog="hive",
    # Optional default schema; datasets may also pass database= to ms.table(...).
    schema="sales_mart",
    user_env="TRINO_USER",
    auth_env="TRINO_AUTH",
)
```

设计约束：

- datasource name 是全局 key，禁止使用 `<domain>.<datasource>`。
- semantic domain 不调用 `ms.datasource(...)`，优先用 `md.ref("datasource.warehouse")` 在 `ms.entity(datasource=warehouse, source=...)` 中引用 kind-qualified datasource id。
- 非机密连接字段写在 datasource 文件里；`user`、`password`、`auth`、`token`、`api_key`、`secret`、`private_key` 等机密字段只能通过 `*_env` 引用环境变量。
- Trino `catalog` 是连接目标；`schema` 只是可选默认 schema，也可以在 `ms.table("orders", database="sales_mart")` 中显式传入。
- datasource 是 entity 的执行来源，不是 metric 的业务口径。

### Dataset

entity 是业务实体或事实表的逻辑视图：

```python
orders = ms.entity(
    name="orders",
    datasource=warehouse,
    source=ms.table("orders"),
    primary_key=["order_id"],
    ai_context=ms.ai_context(business_definition="Order facts."),
)
```

entity 通过结构化 source 指向物理来源。`ms.table(...)` 表达后端表；
不应把 metric 聚合逻辑塞进 entity。

entity 不再接受 Python body，因此不支持在 semantic layer 内用 `backend.sql(...)`
内嵌 SQL view。若 SQL view 已在后端持久化为表/视图，应通过
`source=ms.table(...)` 暴露；一次性 SQL 转换不属于 entity source v1 的 authoring surface。

Snapshot entity declarations expose their partition key through
`versioning=ms.snapshot(...)`. Use this for daily/weekly snapshot tables that
should be observed at the latest available partition by default:

```python
user_profile_daily = ms.entity(
    name="user_profile_daily",
    datasource=warehouse,
    source=ms.table("user_profile_daily"),
    primary_key=["user_id", "dt"],
    versioning=ms.snapshot(
        partition_field=ms.ref("dimension.sales.user_profile_daily.dt"),
        grain="day",
        timezone="Asia/Shanghai",
        format="%Y%m%d",
    ),
)
```

`partition_field` is the declared dimension/time-dimension Ref that carries
the snapshot key. `grain` declares the snapshot cadence (currently `day`). `timezone` resolves
"latest" relative to the requested observe window using a real calendar.
`format` describes the on-disk partition encoding (e.g. `%Y%m%d` for VARCHAR
keys, omitted when the column is already a date). Analysis joins against a
snapshot entity use the partition that matches the observe window end.

## Validity Versioning

Entities representing day-grain SCD2 history may declare validity-interval
versioning. Phase 2 supports the `valid_from` / `valid_to` + `interval` +
`open_end` dialect; `current_flag` is not yet supported.

```python
warehouse = md.ref("datasource.warehouse")
user_history = ms.entity(
    name="user_history",
    datasource=warehouse,
    source=ms.table("user_history"),
    primary_key=["user_id", "valid_from"],
    versioning=ms.validity(
        valid_from=valid_from,
        valid_to=valid_to,
        interval="closed_open",
        open_end=(None, "9999-12-31"),
        timezone="UTC",
    ),
)
```

`valid_from` must be part of `primary_key`. Both `valid_from` and `valid_to`
must reference declared dimensions on the same entity. `open_end` is the tuple
of values that mean "the row is still current" (typically `(None,)` or
`(None, "9999-12-31")`).

The planner subtracts both `valid_from` and `valid_to` from the effective
key when computing relationship safety, so a relationship from a fact
entity to a validity entity can resolve as many-to-one once the validity
table is collapsed to one row per `(key, anchor)`.

### Dimension 和 Time Dimension

dimension 是 row-level 属性，供过滤、分组、relationship 或 metric 表达式复用：

```python
@ms.dimension(entity=orders, ai_context=ms.ai_context(business_definition="Normalized region."))
def region(orders):
    return orders.region.upper()
```

Measure 是 row-level 数值事实，也是 `additivity` 和物理 `unit` 的权威声明点。
Tier-1 metric 默认聚合已验证 measure；derived metric 再通过 composition algebra
传播 unit：

```python
amount = ms.measure_column(
    name="amount",
    entity=orders,
    column="amount",
    additivity="additive",
    unit="CNY",
    ai_context=ms.ai_context(
        business_definition="Order amount in CNY.",
    ),
)

revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
```

time dimension 是特殊 dimension，显式承载时间轴元数据：

```python
dt = ms.time_dimension_column(
    name="dt",
    entity=orders,
    column="dt",
    granularity="day",
    parse=ms.strptime("%Y%m%d"),
)

hh = ms.time_dimension_column(
    name="hh",
    entity=orders,
    column="hh",
    granularity="hour",
    parse=ms.hour_prefix(dt),
)
```

设计约束：

- 需要作为时间窗口、时间粒度或 calendar axis 使用的维度必须声明为 `time_dimension`。
- 普通 `dimension` 不应靠名称如 `dt`、`date`、`event_time` 被自动推断为时间维度。
- `parse` 可省略（此时从列的 ibis dtype 自动推断 parse 变体）。原生 `date`、`datetime`、`timestamp` 列无需显式指定 parse；`string` 或 `integer` 列必须提供 `ms.strptime(format)` 或 `ms.hour_prefix(prefix)`。
- `ms.strptime(format)` 的 `format` 必须是 Python strptime 格式串（`%` 前缀），例如 `"%Y%m%d"`、`"%Y-%m-%d"`、`"%Y%m%d%H"`、`"%Y-%m-%d %H:%M:%S"`。格式串原样传给 backend 的 `date_parse`，作者需按目标 backend 语义书写（参见下一节 `%M` 与 `%i` 注意事项）。原生 `date`/`datetime`/`timestamp` 列不需要 `format`；`ms.hour_prefix` 的字段也不需要 `format`（运行时用 `lpad(2, "0")` 归一化 hour 列）。
- `granularity` 支持 `year` | `quarter` | `month` | `week` | `day` | `hour` | `minute` | `second`。`minute` 和 `second` 要求 `parse` 为 `ms.datetime(...)` 或 `ms.timestamp(...)`；`hour` 在非 `ms.datetime`/`ms.timestamp` 类型上必须使用 `ms.hour_prefix(...)`。省略 `parse` 时，若推断出的 data_type 为 `date`，则 `hour`/`minute`/`second` granularity 会报错。
- body 返回的 ibis dtype 必须与 parse 变体兼容：`.cast("date")` 或原生 date 列 → 省略 `parse`；`.cast("timestamp")` 或原生 timestamp 列 → 省略 `parse` 或使用 `ms.datetime(...)`/`ms.timestamp(...)`。不匹配时执行器 TypeError。
- hour-only 字段（列只存小时数值）必须使用 `ms.hour_prefix(prefix)`，其中 `prefix` 是同 entity 的 day 粒度 time-dimension ref。hour-only 字段支持可选的 `sample_interval`，使其可作为 sampled semi-additive metric 的时间轴。
- 若 metric body 内出现 `.filter(...)`、`.cast(...)` 或多步链式 row-level 中间表达式，且该表达式代表可命名业务概念，应先抽成 `dimension` / `time_dimension` / `measure`，再在 metric 中引用。直接物理列优先使用 `ms.dimension_column` / `ms.time_dimension_column` / `ms.measure_column`。
- `@ms.dimension` / `@ms.time_dimension` / `@ms.measure` 不要求 provenance status。它们的可信度来自所属 entity、row-level 表达式可读性和 materialization 校验。`provenance` 是可选审计字段；缺失时 `describe` 显示 provenance 为 null。`ms.dimension_column` / `ms.time_dimension_column` / `ms.measure_column` 同理。
- `is_default` (optional, default `False`): Mark this dimension as the default time axis
  when the entity has multiple time dimensions. When `observe()` is called without an
  explicit `time_dimension=` argument, the `is_default=True` dimension is used automatically.
  At most one time dimension per entity may carry `is_default=True`; declaring two or
  more raises `SemanticLoadError` with kind `duplicate_default_time_dimension` at assembly
  time.

#### Format specifier divergence: Python strptime vs MySQL/Trino/Presto

`date_format` strings flow unchanged to the backend's `date_parse`
function. Trino and Presto `date_parse` accept MySQL-style format
specifiers, which agree with Python strptime on most common tokens
(`%Y %m %d %H %S %y %j`) but disagree on minutes:

| Specifier | Python strptime | Trino/Presto `date_parse` |
|---|---|---|
| `%M` | Minutes (00..59) | **Month name** (January..December) |
| `%i` | (not used) | Minutes (00..59) |
| `%c` | Locale-dependent datetime | Month, numeric (1..12) |

For minute-granularity string dimensions on Trino/Presto backends, write
`%i` for minutes, not `%M`. Example: `date_format="%Y-%m-%d %H:%i:%S"`.

Marivo does not translate Python strptime to MySQL format. The contract
is: the format string reaches the backend's `date_parse` unchanged;
author with the target backend's MySQL semantics in mind.

Trino additionally does not support these specifiers (per the Trino
datetime functions reference): `%D`, `%U`, `%u`, `%V`, `%w`, `%X`.
Queries using them will fail at the backend with the backend's native
error; Marivo does not pre-validate against backend support.

### Metric

Metric 默认走 tier-1 aggregate：先用 `@ms.measure(...)` 声明并验证 row-level
measure，再用 `ms.aggregate(name=..., measure=..., agg=...)` 生成 metric。
`@ms.metric(...)` 保留为 tier-2 expression-body escape hatch；只有 metric 不能自然表达为
`measure + aggregate` 时才使用。For tier-2 `@ms.metric(...)`, when
`provenance=ms.from_sql(...)` is provided, SQL parity verification is automatically enabled.
Expression-bearing semantic decorators (`@ms.dimension`, `@ms.time_dimension`,
`@ms.measure`, and `@ms.metric`) allow an optional leading function docstring,
then require exactly one `return <ibis expression>` statement; all other body
statements remain invalid.

```python
@ms.measure(
    entity=orders,
    additivity="additive",
    unit="CNY",
    ai_context=ms.ai_context(business_definition="Paid order amount in CNY."),
)
def paid_amount(order_rows):
    return order_rows.filter(is_paid(order_rows)).amount

revenue = ms.aggregate(name="revenue", measure=paid_amount, agg="sum")
```

Tier-2 metric 使用 `entities=[...]` 显式声明依赖。函数 body 的参数只是局部 alias，
按 `entities` 顺序注入 materialized table；参数名不能决定 entity identity。

Derived metrics are direct calls, not decorators. They combine already
registered metrics through a canonical composition and have no Python body:

```python
avg_execution_time = ms.ratio(
    name="avg_execution_time",
    numerator=total_execution_time,
    denominator=query_count,
    unit="s",
    ai_context=ms.ai_context(
        business_definition="Average execution time per query in seconds.",
        guardrails=["Unit is seconds."],
    ),
)
```

Shape classification must fail closed:

- `entities=[...]` non-empty, body uses entity aliases: simple metric (`@ms.metric`).
- `ms.ratio(...)` / `ms.weighted_average(...)` / `ms.linear(...)`: derived metric (body-free).
- `@ms.metric(...)` with an empty entities list: error.
- No `entities` and no composition components: error.

### Metric unit (UCUM)

`@ms.measure` / `@ms.metric` / `ms.aggregate` / `ms.ratio` /
`ms.weighted_average` / `ms.linear` accept optional `unit: str | None`
(default `None`). Values use the UCUM case-sensitive vocabulary, with one explicit
extension: bare ISO 4217 uppercase three-letter codes represent currencies.

| Category | Notation | Examples |
|---|---|---|
| Time | UCUM code | `s`, `ms`, `min`, `h`, `d` |
| Bytes | UCUM code | `By`, `KiBy`, `MiBy` |
| Percent | UCUM code | `%` (values are percentage points, e.g. `89.8`) |
| Dimensionless fraction | UCUM code | `1` (values 0–1, native output of ratio decompositions) |
| Counted noun | UCUM annotation, English singular | `{order}`, `{user}` |
| Compound / ratio | UCUM `/` combination | `By/s`, `{order}/d`, `CNY/{user}` |
| Currency (explicit extension) | Bare ISO 4217 uppercase code | `CNY`, `USD` |

**Authoritative declaration site:** declare `unit=` on the measure dimension.
Tier-1 and derived metrics inherit it automatically at load; pass `unit=` on a
metric only to override the derived value. For tier-2 (`@ms.metric`), there
is no measure to derive from, so `unit=` is the direct declaration.

**Tier-1 derivation (from measure):**

| aggregation | unit result |
|---|---|
| `sum` / `min` / `max` / `mean` / `median` / `percentile` | `measure.unit` (preserved) |
| `count` / `count_distinct` | `None` (counted noun is content-specific; author declares `{order}` explicitly) |

For `ms.aggregate(..., agg=("percentile", q))`, backend compilation follows
available Ibis support. Trino materializes this aggregation with approximate
percentile semantics (`APPROX_PERCENTILE`) rather than exact quantile SQL.

**Derived derivation (from components):**

| composition | rule | on failure |
|---|---|---|
| `ratio(num, denom)` | both units known and equal → `"1"` (dimensionless) | `None` (no compound unit constructed) |
| `weighted_average(value, weight)` | `value.unit` | `None` if value unit missing |
| `linear(terms)` | all terms known and equal → that unit | `None` if any `None`; **error** if ≥2 distinct known units |

Author override always wins: if `unit=` is declared on a metric, the loader does
not replace it. `unit` stays optional everywhere — it never affects computed values,
only metadata.

**Linear commensurability:** adding incommensurable units (e.g. `CNY + {order}`)
is a dimensional error. The validator raises `INCOMMENSURABLE_LINEAR_UNITS` /
constraint `linear_unit_commensurable` for linear metrics whose terms carry ≥2
distinct known units. An author override on the linear metric does not suppress
this error — the physics of addition is independent of labelling.

Iron rules: unit precisely describes the metric's emitted values; no layer may
convert values based on unit; `None` is always valid (richness advisory only, not
a readiness blocker). Validation at authoring time is lightweight: non-empty,
every character falls within `0x21–0x7E`. Full UCUM grammar validation is a
non-goal. Design docs: `docs/superpowers/specs/2026-06-11-metric-unit-design.md`,
`docs/superpowers/specs/2026-06-16-metric-unit-measure-propagation-design.md`.

### Base Metric Grain And Additivity

Every base metric must declare `additivity`. Single-entity base metrics may
omit `root_entity`; Marivo resolves it to the only entity. Multi-entity base
metrics must declare `root_entity` explicitly. The root entity defines the
preserved row set, join anchor, and observe time axis.

```python
@ms.metric(
    entities=[orders, users],
    root_entity=orders,
    additivity="additive",
)
def revenue(orders, users):
    return orders.amount.sum()
```

Joined entities may provide dimensions and filters, but aggregate receivers in
a base metric body must belong to the root entity.

### Base Metric Fan-Out Policy

`@ms.metric(...)` accepts an optional kwarg:

- `fanout_policy: Literal["block", "aggregate_then_join"] = "block"` — fan-out
  policy on the metric. `"block"` (default) rejects unsafe one-to-many edges
  with an `unsafe-fanout` repair payload that names both `set_metric_root` and
  `set_fanout_policy` as candidate fixes. `"aggregate_then_join"` reduces the
  unsafe-side entity to the merge grain (root primary key plus the requested
  non-root dimensions/filters that target that side) before the join. Requires
  `additivity in {"additive", "semi_additive"}` and is rejected on derived
  metrics; the kwarg is authored only on `@ms.metric` (relationships, entities,
  and `observe(...)` reject it).

```python
@ms.metric(
    entities=[orders, order_items],
    root_entity=orders,
    additivity="additive",
    fanout_policy="aggregate_then_join",
)
def gmv_with_items(orders, order_items):
    return orders.amount.sum()
```

### Sampled Semi-Additive Metrics

Use sampled folds for periodic snapshot facts such as bandwidth, capacity, inventory, or device-reported rates. The time dimension declares physical precision with `granularity` and reporting cadence with `sample_interval`; the metric declares the business status axis and fold. `sample_interval` is supported on native `ms.datetime(...)` / `ms.timestamp(...)` parses, on string/integer `ms.strptime(...)` parses, and on `ms.hour_prefix(...)` parses.

```python
sample_ts = ms.time_dimension_column(
    name="sample_ts",
    entity=bw_samples,
    column="sample_ts",
    granularity="second",
    parse=ms.timestamp(timezone="UTC", sample_interval=(5, "minute")),  # explicit source override
)

@ms.metric(
    entities=[bw_samples],
    additivity="semi_additive",
    time_fold="mean",
    status_time_dimension=sample_ts,
    unit="kbit/s",
)
def upstream_bw(bw_samples):
    return bw_samples.upstream_kbps.sum()
```

The metric body expresses the spatial aggregate inside one sample point. `status_time_dimension` binds the metric's business as-of/status time axis. `sample_interval` on that time dimension means the axis is a fixed-cadence sampled series, and `time_fold` expresses how the sample-point series is reduced to the requested observe grain. P95-style folds use `time_fold=("quantile", 0.95)` and are always recomputed from base samples for the requested grain.

Not every semi-additive metric is sampled. Already-summarized snapshot or
status facts, such as daily inventory, omit `time_fold` but must still declare
`status_time_dimension` on the metric. `versioning=ms.snapshot(...)` /
`ms.validity(...)` and `is_default=True` time dimensions can document entity
behavior, but they do not replace the metric-level status axis. A bare
`additivity="semi_additive"` metric without `status_time_dimension` is invalid.

```python
inventory_daily = ms.entity(
    name="inventory_daily",
    datasource=warehouse,
    source=ms.table("inventory_daily"),
    primary_key=["sku_id", "warehouse_id", "dt"],
    versioning=ms.snapshot(
        partition_field=ms.ref("time_dimension.sales.inventory_daily.snapshot_date"),
        grain="day",
        timezone="UTC",
        format="%Y%m%d",
    ),
)

snapshot_date = ms.time_dimension_column(
    name="snapshot_date",
    entity=inventory_daily,
    column="dt",
    granularity="day",
    parse=ms.strptime("%Y%m%d"),
    is_default=True,
)

@ms.metric(
    entities=[inventory_daily],
    additivity="semi_additive",
    status_time_dimension=snapshot_date,
)
def on_hand_units(inventory_daily):
    return inventory_daily.on_hand_units.sum()
```

`status_time_dimension` must be the business time at which the metric value is
valid, such as `snapshot_date`, `as_of_date`, or `state_date`. Do not use
`created_at`, `updated_at`, or `ingest_time` unless that dimension is truly the
business as-of time for the status fact; technical write times make historical
as-of queries drift when data is backfilled or reprocessed.

### Relationship

relationship 描述 entity 之间的连接路径：

```python
# models/semantic/sales/_domain.py
import marivo.semantic as ms

ms.domain(name="sales", owner="Mina Zhang")

# orders, customers, order_customer_id, and customer_id are declared earlier
# in this _domain.py.
ms.relationship(
    name="orders_to_customers",
    from_entity=orders,
    to_entity=customers,
    keys=[ms.join_on(order_customer_id, customer_id)],
)
```

目标态 relationship 是纯 metadata 顶级调用。连接键必须使用 `dimension` / `time_dimension` 的 ref 引用，不能使用裸字符串物理列名。`from_columns` / `to_columns` / `from_fields` / `to_fields` 不应作为 alias 继续保留；目标态只接受 `keys=[ms.join_on(...)]`，值为 decorated dimension refs。

### Decomposition

decomposition 描述 metric 在变化归因中的数学结构，不等同于 SQL aggregation：

| Builder | 适用 metric | 组件要求 |
| --- | --- | --- |
| `ms.sum()` | 可加总数量，如 revenue、orders、users | 无组件 |
| `ms.ratio(numerator=..., denominator=...)` | 比例/转化率，如 conversion_rate | numerator 和 denominator 都是 metric ref |
| `ms.weighted_average(value=..., weight=...)` | ratio-of-sums 或带权均值，如 ARPU | numerator 和 weight 都是 metric ref |

Metric bodies cannot call decorated metric functions to express derived metrics.
Derived metric component roles come entirely from their decomposition builder:

| Builder | Component keys | Author-facing shape |
| --- | --- | --- |
| `ms.ratio(numerator=..., denominator=...)` | `numerator`, `denominator` | `ms.ratio(name=..., numerator=..., denominator=...)` |
| `ms.weighted_average(value=..., weight=...)` | `numerator`, `weight` | `ms.weighted_average(name=..., value=..., weight=...)` |

The `weighted_average(value=...)` argument intentionally stores the internal key
`numerator`. Analysis output already uses that role name, and body-free
authoring means users no longer type the internal key.

Derived metrics do not perform Python-side zero-division handling; the generated
Ibis expression follows the target backend's SQL semantics. Most backends return
`NULL` when a denominator is zero. If a metric needs an explicit fallback, first
wrap that behavior in a base metric, then reference the base metric from the
derived decomposition.

If a derived calculation needs dimension/time_dimension or row-level intermediate values,
first package those values as base metrics. Derived metrics cannot directly
reference entities, dimensions, or time dimensions.

### Provenance

Metric provenance is declared as `provenance=ms.from_sql(sql=..., dialect=...)`.
`verification_mode` is inferred automatically: when `provenance` is present,
the metric enables SQL parity verification (`"sql_parity"`); when absent,
the metric is trusted as semantically expressed (no verification needed).

| Provenance | Meaning |
| --- | --- |
| `provenance=ms.from_sql(...)` | Migrated from SQL/BI/knowledge base; parity verification enabled |
| (no `provenance`) | Python/Ibis is the sole business source; trusted as verified |

`provenance` is single-dialect. If a metric needs multi-dialect
verification, use fixture-based parity tests instead of stuffing multiple SQL
statements into the decorator.

When `provenance` is provided, the metric's parity status starts as `unverified`
and becomes `verified` once `parity_check()` succeeds, or `drifted` if values
mismatch. When no `provenance` is provided, the metric is immediately `verified`
(trust the semantic body). Agents and downstream analysis frames can inspect
the computed parity status (`verified`, `unverified`, or `drifted`).

Derived metrics must omit `provenance`. A derived metric
cannot be directly parity-checked; its effective verification status propagates
from component metrics.

Derived metric parity status propagates from component statuses:

- All components `verified` → result is `verified`.
- Any component `drifted` → result is `drifted`.
- Any component `unverified` → result is `unverified`, unless already `drifted`.

## Agent 工作流

### 1. 先读取现状

agent 在新增或修改语义前应先运行确定性的 check 或读取当前 registry。Python-only 目标态首选显式 project load：

```python
import marivo.semantic as ms

project = ms.SemanticProject(root="marivo/semantic")
result = project.load()
if result.errors:
    raise SystemExit(result.errors)
```

`project.load()` / 后续 check helper 要求：

- 缺省向上查找最近的 `models/semantic/`，找不到时 fail closed 并提示显式传入 project root。
- 使用 fresh interpreter 加载项目，避免 namespace package 和模块缓存影响修复循环。
- 打印所有 decorator / load / assembly errors，包含结构化 kind、refs、location、hint 和人类可读摘要。
- 非零退出码表示存在未解决错误。
- 可选 `--parity` 对所有声明了 `provenance` 的 metric 运行 parity。
- 可选 `--strict-provenance` 将任何 `unverified` metric 视为非零退出。检查 metric 自身 provenance status 和 derived metric 的传播 status；任一非 `verified` / `python_native` 都触发。例如 derived metric 自身已 `python_native` 但某个 component 仍 `unverified` 时同样退出，避免 agent 误以为"提升自己就够了"。
- 默认列出所有字符串 refs 和 unverified metrics，作为 agent 需要复核的 warning。
- 支持 `.venv/bin/python -m marivo.semantic.check --format=json --readiness` 输出结构化 errors / warnings / readiness report / parity summary，便于 agent 稳定解析。

需要探索对象时，再用项目显式 API。agent 进入一个新 repo 后的默认入口是 `ms.load()`（内部经 `find_project` 向上查找 `models/semantic/`，该函数不是公开 API）；找不到时不要猜 root，应提示初始化或显式传入 project root。

### 2. 声明最小业务对象

新增 metric 时的最小 happy path 是 datasource、entity、metric 和 decomposition。只有当分析需要时间窗口、过滤复用或跨表关系时，再渐进加入 time_dimension、dimension 和 relationship。表级证据首选 `md.discover_entity(md.ref("datasource.warehouse"), md.table(...))` 等 `md.discover_*` 系列；`table.schema()` 只能作为类型兜底，不能替代表注释、列注释、nullable 和分区信息。

新建 metric 可以省略 provenance 并自动进入 `unverified`，但 agent 不能把它当作完成状态。若同一 PR 新增多个 unverified metrics，应停下来确认业务来源；CI 可用 `--strict-provenance` 禁止 unverified metric 合入。

### 3. Reload 并处理结构化错误

修改 authoring 文件后，应优先运行 `check`。REPL 中可调用 `project.load()`，但 agent fix loop 不应依赖 thread-local active project 或上一次 import 的模块缓存。遇到 `SemanticDecoratorError`、`SemanticLoadError`、`SemanticRuntimeError`、`SemanticParityError` 时，优先按错误中的 kind、refs、hint 和 source location 修改定义，不要用 try/except 隐藏错误。

### 4. Materialize 或交给 analysis

语义层自身只产出 Ibis object。实际分析应由 `analysis` operator 或上层 session 执行：

```python
import marivo.analysis as mv

session = mv.session.get_or_create(name="revenue-investigation")
frame = session.observe(session.catalog.get("metric.sales.revenue"))
print(frame.summary())
```

目标态边界是：`semantic` 负责“对象是什么、口径是什么、如何物化”；`analysis` 负责“对这些对象执行 observe/compare/decompose/detect/correlate 等分析步骤并持久化 artifact/lineage”。

## Agent 决策规则

### Field vs Metric

| 问题 | 选择 |
| --- | --- |
| 每一行都能从一个物理列直接读取，例如国家、平台、订单日期 | `ms.dimension_column` 或 `ms.time_dimension_column` |
| 每一行都能从一个物理列直接读取的数值事实，例如 amount、quantity、bytes | `ms.measure_column`，然后用 `ms.aggregate` 生成 metric |
| 每一行都能计算出来但需要 Ibis 表达式，例如 normalize、case、cast、跨列相减 | `@ms.dimension`、`@ms.time_dimension` 或 `@ms.measure` 作为 expression escape hatch |
| 只是 metric 内部的一段条件表达式，不需要下游引用 | 可直接写在 metric Ibis 表达式内 |
| 会被多个 metric、filter、relationship 或分析 slice 复用 | 提升为 dimension/time_dimension |

为了让 agent 能机械执行，目标态再加一条硬规则：metric body 内只允许聚合表达式和对已声明 dimension/time_dimension/measure 的引用。凡是 row-level `.filter(...)`、`.cast(...)`、复杂 `case`、多步链式中间值，默认先抽成 `dimension`/`time_dimension`/`measure`；直接物理列访问优先使用 `ms.dimension_column`/`ms.time_dimension_column`/`ms.measure_column`。只有一次性且无业务命名价值的简单列访问可以留在 metric body。

### Sum vs Ratio vs Weighted Average

| Metric 形态 | Decomposition |
| --- | --- |
| 可直接加总的绝对量 | `ms.sum()` |
| `numerator / denominator`，如转化率、成功率 | `ms.ratio(numerator=..., denominator=...)` |
| 分段均值需要权重解释 mix effect | `ms.weighted_average(value=..., weight=...)` |

如果 agent 不确定 decomposition，不能随便填 `ms.sum()`。应先从业务定义、source SQL、已有 metric components 或用户确认中确定结构。

### 什么时候使用 ref

目标态优先使用 decorated object refs，因为它能让 Python 静态阅读和重构更直接。
`ms.ref("<kind>.<semantic_id>")` 只用于无法自然 import 的前向引用、跨 domain 引用或
工具生成场景；裸字符串如 `"sales.revenue"` 不允许作为 authoring 参数引用。

```python
sessions_per_user = ms.ratio(
    name="sessions_per_user",
    numerator=ms.ref("metric.marketing.sessions"),
    denominator=total_users,
)
```

`ms.ref(...)` 的唯一位置参数使用 `<kind>.<semantic_id>` 格式。例：

- `ms.ref("metric.marketing.sessions")`
- `ms.ref("entity.sales.orders")`
- `ms.ref("dimension.sales.orders.user_id")`
- `ms.ref("time_dimension.sales.orders.created_at")`

跨 domain refs 允许，但必须在 resolve 阶段做存在性、cycle 和 contract 检查；不能退回到 SQL provenance 里复制另一个 domain 的定义。

因为 fallback ref 仍由字符串字面量构造，`check` 默认应列出 `ms.ref(...)` refs，并标记为 `potentially_fragile_reference`。目标态还应提供结构化重命名 helper，让 agent 优先通过工具修改 semantic refs，而不是手工 grep。

Refactor helper 契约：

```python
project.refactor.rename("metric", "sales.old_revenue", "sales.revenue", write=False)
project.refactor.rename("dimension", "sales.orders.old_user_id", "sales.orders.user_id", write=True)
```

- 缺省 dry-run，输出 unified diff 和变更文件列表；只有 `--write` 才落盘。
- 输入是 `<kind> <old-fqn> <new-fqn>`，`kind` 至少覆盖 `domain`、`datasource`、`entity`、`dimension`、`time_dimension`、`metric`、`relationship`。
- 覆盖范围包括 decorator / metadata call 的 `name=`、`domain=` 必要改动、`ms.ref(...)` 字符串、relationship endpoint refs、`keys` / `ms.join_on(...)`、decomposition component refs、`_exports.py` re-export。
- 不修改 `provenance` 中的 SQL 或自然语言 prose；这些字段需要人工 review。

## 验证与失败语义

Python 语义层使用多层 fail-closed 验证。

### Decorator-time

decorator 执行时检查局部声明是否自洽：

- domain/datasource/entity/dimension/metric 重名。
- decorated ref 类型错误。
- 跨 domain / 跨 entity ref 不合法。
- expression-bearing decorator 缺少显式 `domain=`，且所在加载上下文没有显式 default domain。此错误只在 `_domain.py` 显式 `default=False`，或对象声明在 domain 目录之外的文件中时触发；缺省 `default=True` 场景下，同目录对象自然继承 domain，不会进入此错误路径。
- base metric 缺少 `entities=[...]`。
- derived metric 带 entity 参数、缺少 decomposition components 或在 body 中读取 entity table。
- decorator / metadata call 出现在 semantic loader context 之外。
- metric 函数体不满足单 return 表达式约束。
- metric body 调用 decorated metric 函数、legacy component-body calls, or Ibis SQL escape hatches.

`outside_loader_context` 错误必须带可执行 hint：把定义移动到 `<project_root>/models/semantic/<model>/<file>.py`，然后用 `SemanticProject(root="<project_root>/marivo/semantic").load()` 重新加载；如果是在 notebook 中探索，使用 scratch Ibis expressions，只有注册对象才走 semantic loader。

常见 structured error 到 agent action 的映射应足够机械：

| Error kind | Agent action |
| --- | --- |
| `duplicate_name` | 检查同一 domain 内是否重复声明；删除旧声明或改 `name=`，再运行 check |
| `missing_domain` | 在 `<root>/<domain>/_domain.py` 增加 `ms.domain(name=...)`，或在对象声明上补 `domain=ms.domain(name=...)` 的返回值 |
| `missing_entity_ref` | 确认 entity 已声明；若跨文件前向引用，改用 decorated ref 或 `ms.ref(...)` |
| `cross_model_reference` | 优先通过 `_exports.py` 导入；没有 `_exports.py` 时可直接 import sibling file 或使用显式 `ms.ref(...)` |
| `invalid_decomposition` | 检查 `ms.ratio(...)` / `ms.weighted_average(...)` 的 components 是否都指向已注册 metric |
| `invalid_component_body` | Remove ms.component() from metric body; use `ms.ratio`/`ms.weighted_average`/`ms.linear` instead |
| `outside_loader_context` | 把定义移到 `<root>/models/semantic/<model>/<file>.py`；notebook 探索改用 scratch Ibis 表达式 |
| `unverified_provenance` | 若要进入 strict workflow，补 `provenance=ms.from_sql(...)` triple、改为 `python_native`，或先停止并确认业务口径 |
| `sql_escape_hatch` | 把 raw SQL 移到后端持久视图并通过 `ms.table(...)` 暴露；metric body 保持 Ibis expression |

### Load / Assembly-time

loader 执行项目文件后，assembly validation 检查跨对象关系：

- `_domain.py` 缺失或 domain 注册不匹配目录。
- `ms.domain(...)` 出现在非 `<root>/<domain>/_domain.py` 文件，或一个 `_domain.py` 声明多个 domain。
- entity 引用不存在的 datasource。
- metric 引用不存在的 entity 或 decomposition component。
- cross-domain `ms.ref(...)` 不存在、对象类型不匹配或形成循环依赖。
- `entities=[...]` 注入顺序与 metric 函数参数数量不一致。
- hour time dimension 缺少 required prefix。
- relationship endpoint、join dimension refs、dimension entity membership 或 arity 不合法。

失败后 registry 进入 `errored`，并保留 `load_errors`。agent 应修复所有结构化错误后重新加载。

### Runtime / Materialization-time

materialization 执行用户函数并组合 Ibis object。失败可能来自 backend factory、Ibis table/column 不存在、用户函数运行时异常或表达式不兼容。

这类错误不应被转成“找不到 metric”。已注册对象不存在时使用 not-found 错误；对象存在但执行失败时使用 runtime error。

### Parity-time

parity 是 SQL provenance 与 Ibis 表达式的可比性检查。失败可能来自：

- source SQL 缺失或 dialect 缺失。
- metric 仍为 `unverified` 且当前 parity / CI 策略要求可信 provenance。
- datasource profile 缺失、backend type 不受支持，或 live backend 与
  profile 配置不一致。
- source SQL 或 metric expression 无法执行。
- 任一侧不是 scalar。
- scalar 值不相等。

parity 失败时，agent 应先定位语义差异，不应直接调大 tolerance。

### Static policy-time

目标态还应有不依赖数据执行的静态 policy 检查：

- entity primary key metadata 可选开启 sample uniqueness check；默认不阻塞加载，但 check 输出应把未验证 PK 标为 warning。
- metric body 禁止 `backend.sql(...)`、Ibis raw SQL escape hatch 或 dialect-specific SQL snippets。跨 dialect 的 vendor 差异应通过 datasource/backend compilation 和 parity 暴露，而不是藏在 metric body。
- SQL escape hatch 检查在 materialize-time 扫描 Ibis expression tree 中的 raw SQL node；decorator-time 只做显式 `backend.sql(...)`、`.raw_sql(...)` 等明显方法名的早期拒绝，避免仅靠 AST 误伤普通列名。

## 与历史 schema 参考的关系

旧 schema 设计提供了语义参考：semantic domain、entity、dimension、relationship、metric、time granularity、AI context 和 MARIVO decomposition extensions。Python 语义层借鉴这些对象边界，但不被旧链路约束。

本文档采用以下边界：

- Python 文件是 Python-native track 的 source of truth。
- 已删除链路的 YAML/JSON 和 metadata store 不是本文档要求的兼容目标。
- 本文档不承诺 Python 定义与旧 schema 文档双向转换。
- 本文档不要求把 Python semantic definitions 持久化到旧 metadata store。
- 旧 schema 的对象语义可作为命名、decomposition、time-field metadata 的参考，但 Python API 可以为 agent ergonomics 做不同取舍。

## 与 analysis 的关系

`semantic` 和 `analysis` 是 Python-native Marivo 的两段式架构：

```text
semantic:  datasource / entity / dimension / metric / relationship
      ↓
Ibis materialization + typed semantic refs
      ↓
analysis: observe / compare / decompose / detect / correlate / ...
      ↓
typed frames + session persistence + lineage
```

设计边界：

- `semantic` 不产出 `MetricFrame`、`DeltaFrame` 或 attribution artifact。
- `analysis` 不重新定义 metric 口径，不猜 entity/time dimension，不绕过 semantic registry 直接读表。
- backend ownership 位于 profile/session/execution 层；semantic object 只声明
  datasource 名称引用，不声明 backend type 或连接字段。
- 下游 analysis operator 应通过 semantic refs 读取对象，例如 `sales.revenue`，并通过 materialization 获得 Ibis expression。

如果一个分析需要新的业务对象，应先扩展 `semantic`，再让 `analysis` 消费它；不应把业务定义隐藏在一次性 analysis script 中。

## v1 已落地边界

当前 `models/semantic` 已经提供以下能力：

- `SemanticProject`、project-scoped registry、context-local active registry。
- decorators：`domain`、`datasource`、`entity`、`dimension`、`time_dimension`、`metric`、`relationship`。
- builders：`sum`、`ratio`、`weighted_average`、`ref`。
- loader：domain 目录扫描、`_domain.py` 优先执行、sibling files 排序执行、re-load 清理项目模块。
- reader/introspection：`ms.load()` 返回 `SemanticCatalog`；`catalog.list(...)`、`catalog.get(...)`、`catalog.preview(...)`、`catalog.readiness(...)` 是 agent-facing read/handoff surface。
- materialization：entity、dimension、metric 到 Ibis object 是 semantic internals / analysis runtime 的实现细节。
- validation：metric body AST 约束、missing refs、time prefix、relationship endpoint/columns/arity。
- SQL provenance：`provenance=ms.from_sql(...)`。
- parity helper：`compare_metric_to_provenance` 和 `ParityResult`。
- structured errors：decorator、assembly、runtime、parity、load errors。

当前 v1 仍保留若干 agent footguns：domain 归属部分依赖 loader 上下文，metric entity 依赖来自函数参数名，datasource / relationship 是 decorator body 形态，reader 主要是 free functions，provenance 不是必填状态，loader 行为仍暴露 sibling file sort order。这些是目标态要移除的兼容负担，不应继续固化为长期设计。

## v1.1 Breaking 目标

以下目标应作为下一轮实现的破坏性契约调整，而不是长期后续愿望：

- 所有语义对象使用显式 `domain=` 或显式 default domain；文件位置只做 discovery 和组织校验。
- `ms.domain(...)` 只能出现在 `<root>/<domain>/_domain.py`，且 `name` 必须等于目录名；`owner` 必填并记录负责人姓名；`default` 缺省为 `True`，允许同目录对象省略重复 `domain=`。
- 标准 agent authoring pipeline 使用 `_domain.py` 单文件；对象变多时仍按依赖顺序在
  `_domain.py` 内维护。feature-oriented sibling files 只能作为另行设计的多文件
  authoring 模式。
- Metric 显式 `entities=[...]`；函数参数名只做局部 alias。
- Base metric uses `@ms.metric(...)`; derived metric uses body-free `ms.ratio(...)` / `ms.weighted_average(...)` / `ms.linear(...)`, relying on composition components.
- Composition component roles come from `ms.ratio(...)` and `ms.weighted_average(...)` and `ms.linear(...)` builders.
- Derived metrics do not have Python bodies; custom derived arithmetic must be expressed through base component metrics.
- Derived metric 的有效 parity status 从自身 provenance 和 components status 中取更弱者。
- Datasource 和 relationship 改为顶级 metadata call，不再要求无意义 function body。
- Relationship join keys 改为 `keys=[ms.join_on(...)]`，值为 dimension/time_dimension refs；裸字符串 `from_columns` / `to_columns` 和旧 `from_fields` / `to_fields` 不再是目标态契约。
- Reader 主 API 迁移到 `SemanticCatalog`；free functions 只保留为有显式 active project 的 REPL sugar。
- Metric provenance status 始终存在；authoring-time 缺省为 `unverified`，promotion / strict CI / analysis consumption 前必须提升为 SQL triple 或 `python_native`。
- Parity status 成为 metric / frame / describe 的可见属性。
- Dimension / time_dimension 不要求 provenance status；缺失 provenance 在 describe 中显示为 `null`。
- Dataset 不支持 Python body SQL view；持久化 SQL view 应作为普通 table source authoring。
- 提供 Python-only check helper，显式加载 `SemanticProject` 并返回结构化 errors / warnings。
- `check` 缺省向上查找 `models/semantic/`，支持 `--strict-provenance`，并默认提示字符串 refs。
- 提供 semantic refactor rename 工具，减少 agent 手工重命名字符串 refs。
- Loader 采用 two-pass collect / resolve；合法 ref 不受 sibling filename sort order 影响。
- 内部 loader 的 `find_project` 向上查找 `models/semantic/`，由公开入口 `ms.load()` 调用；它本身不是公开 API。
- Reader 增加 catalog browsing、single-object details、preview 和 readiness handoff。
- `describe(..., compile_sql=True)` 返回结构化对象，包含 Ibis repr、compiled SQL、source SQL、dependencies、source location 和 parity status；`format="text"` 只作阅读糖。
- `name=` 是 semantic identity；Python 符号名只是 local alias，check/describe 显示二者映射。
- structured errors 提供 error kind 到 agent action 的稳定映射。
- `ai_context` 收敛为固定 schema，适用于所有语义对象，至少包含 `business_definition` 和 `guardrails`；禁止不可消费的自由 dict。

## 后续演进

以下是 v1.1 之后的方向：

- 更完整的 relationship-aware materialization 和 cross-entity filter resolution。
- entity primary / unique key 的可配置 sample validation。
- 更丰富的 generated SQL diff 和 parity fixture lifecycle。
- 明确 Ibis 跨 dialect 表达式失败的分类：普通 Ibis 表达式编译失败、raw SQL escape、backend capability gap 应返回不同 structured error。
- 面向 agent 的例子库和 skill 文档，与 public API drift check 绑定。

新增演进必须保持一个原则：不要引入与 Python authoring source of truth 冲突的第二套业务口径定义，也不要把隐式推断重新包装成 agent ergonomic shortcut。

## 测试与维护

修改 Python 语义层行为后，应使用仓库 entrypoints 验证。针对当前 semantic 的常用聚焦命令是：

```bash
make test TESTS=tests/test_semantic_decorators.py
make test TESTS=tests/test_semantic_materialization.py
make test TESTS=tests/test_semantic_parity.py
```

文档-only 修改至少应运行：

```bash
git diff --check -- docs/specs/semantic/python-semantic-layer.md
```

维护规则：

- 文档里的 current-state API 必须与 `marivo.semantic` 公开导出对齐。
- 示例不能使用 bare `python`、`pytest`、`mypy` 或 `ruff`。
- 目标态能力必须明确标注为目标态或后续演进。
- 不要把已删除链路的兼容性承诺写进 Python-native 设计文档。
