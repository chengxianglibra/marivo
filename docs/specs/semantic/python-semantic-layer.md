# Marivo Python 语义层总体设计

状态：draft design。本文描述 `marivo.semantic_py` 作为 Marivo Python 库语义层的目标态设计、当前 v1 边界和 agent 使用契约。它是设计侧文档，不替代 `osi-marivo-spec/` 的 schema，也不表示所有目标态能力都已经实现。

本文面向 Claude Code、Codex 等通用 coding agent。设计目标不是让 agent 记住一套私有 DSL，而是让 agent 能像维护普通 Python 项目一样维护业务语义：读取现有对象、声明明确模型、用 Ibis 表达计算口径、保留 SQL 来源、运行校验，并把稳定 semantic refs 交给 `marivo.analysis_py` 消费。

## 设计目标

`marivo.semantic_py` 是 Python-native 分析链路的业务对象契约。它回答的是“这个分析项目里有哪些可被稳定引用的业务对象”，而不是“如何把 YAML、SQL 或运行时 API 包成另一个入口”。

目标态满足以下要求：

- Python 文件是语义定义的 source of truth。agent 修改业务口径时应改 Python authoring 文件，而不是编辑生成物或运行时存储。
- Datasource 是项目级可分享配置，定义在 `.marivo/datasource/*.py`；semantic model 只通过全局 datasource name 引用它。
- 语义对象必须可被通用 agent 静态阅读：dataset、field、time field、metric、relationship、decomposition 和 provenance 都有显式 Python 声明。
- 业务口径不能靠字段名、表名或自然语言自动猜测。agent 必须通过 decorated refs、函数签名、`source_sql` / `source_dialect` / `source_document`、parity result 和结构化错误来收敛。
- 归属、依赖和项目边界必须来自显式声明或显式 default model。model 不能由文件路径猜测，metric 不能由函数参数名推断 dataset，reader 不能靠 thread-local active project 隐式选项目。
- Ibis 是 Python 语义层唯一表达计算口径的执行表达式层。SQL 可以作为 provenance 和 parity oracle 保留，但不作为主要 authoring 语言。
- `analysis_py`、后续 operator、skill 或脚本只消费稳定 semantic refs 和 materialized Ibis 表达式，不直接依赖用户项目内的 Python 文件布局细节。
- 失败语义 fail closed。装饰、加载、组装、物化、parity 任一阶段无法证明契约成立时，应给出结构化错误，而不是降级为 best-effort 猜测。

核心判断标准是：如果一个业务对象会被下游分析引用，它必须先进入语义层；如果一个规则只存在于 agent 的临时提示词或 SQL 草稿里，它还不是稳定语义。

## Authoring 快速路径

目标态支持 single-file 快速路径。agent 可以先在 `.marivo/semantic/sales/_model.py` 中完成从 dataset 到 metric 的最小声明；项目 datasource 单独放在 `.marivo/datasource/warehouse.py`。当模型变大时，再把相关对象拆到 sibling `.py` 文件中，而不需要改变已有 semantic ids。

```python
# .marivo/datasource/warehouse.py
import marivo.datasource_py as md

md.datasource(
    name="warehouse",
    backend_type="duckdb",
    path="/data/warehouse.duckdb",
)
```

```python
# .marivo/semantic/sales/_model.py
import marivo.semantic_py as ms

ms.model(name="sales", description="Sales analytics")

@ms.dataset(
    name="orders",
    datasource="warehouse",
    primary_key=["order_id"],
    description="Order facts.",
    ai_context={
        "business_definition": "One row per order before metric-level filters.",
        "guardrails": ["Do not treat this as paid orders only."],
    },
)
def orders(backend):
    return backend.table("orders")

@ms.field(dataset=orders, description="Paid order flag.")
def is_paid(orders):
    return orders.pay_status == 1

@ms.metric(
    datasets=[orders],
    decomposition=ms.sum(),
    description="Paid revenue.",
    source_sql="select sum(amount) as value from orders where pay_status = 1",
    source_dialect="duckdb",
    source_document="kb://sales/revenue",
    ai_context={
        "business_definition": "Total order amount for paid orders only.",
        "guardrails": ["Excludes unpaid orders.", "Does not net out refunds."],
        "synonyms": ["gmv", "paid sales"],
        "examples": ["What was paid revenue last week?"],
    },
)
def revenue(order_rows):
    return order_rows.filter(is_paid(order_rows)).amount.sum()
```

这一层的输出不是 pandas DataFrame 或 SQL 字符串，而是可加载的 Python 定义。

### 通用 Authoring 规则

- `name=` 给出时是唯一 semantic identity。
- `name=` 省略时，Python 变量名或函数名作为 fallback identity。
- Python 符号名只是 local alias，不参与 semantic id。
- `description=` 是短标签或一行说明；`ai_context.business_definition` 是完整业务定义，可多行，agent 用它判断对象是否匹配用户意图。
- `ai_context` schema 适用于 model、project datasource、dataset、field、time_field、metric 和 relationship 所有对象。所有字段可选，缺失时 `describe` 返回 `null` 或空列表。
- `ai_context` 固定字段是 `business_definition: str | None`、`guardrails: list[str]`、`synonyms: list[str]`、`examples: list[str]`、`instructions: str | None`、`owner_notes: str | None`。
- `business_definition` 和 `guardrails` 对 dataset 与 metric 最重要；跨 model 引用前，agent 应优先读取这两个字段判断是否可复用。
- `examples` 只放自然语言示例问法，不放 SQL、Ibis snippet 或 expected values。
- 未知 `ai_context` 字段 fail closed，避免 agent 把不可消费内容塞进语义契约。

## Registry / Loader

`SemanticProject` 指向一个语义项目根目录。loader 执行受信任的本地 Python 文件，把 decorators 的副作用组装成内存 registry：

```text
semantic/
  sales/
    _model.py          # 可作为 single-file quick path
    revenue_metrics.py # 推荐按业务主题拆分
    retention.py
  marketing/
    _model.py
    _exports.py
```

目标态 loader 规则是：

- 每个 model 必须在 `<root>/<model>/_model.py` 中调用一次 `ms.model(name="<model>", ...)`。
- `_model.py` 是该 model 的 entrypoint，可以只声明 model metadata，也可以承载 single-file 快速路径中的 datasource、dataset、field、metric 和 relationship；但不能声明多个 model，也不能用与目录名不同的 `name`。
- `ms.model(default=...)` 缺省为 `True`。默认场景下，同目录 sibling files 里的对象可以省略重复 `model=`；如果项目希望 review 时强制每个对象显式写 `model=`，可在 `_model.py` 里传 `default=False`。
- default model 作用域仅限当前 model 目录的顶层 sibling files，不向子目录传播。`sales/subdomain/*.py` 不继承 `sales/_model.py` 的 default；子目录若要被加载，应作为独立 model 域或由项目明确扩展 loader 规则。
- default model 是 loader 在加载该 model 目录时的上下文，不随 `from x import *` 或普通 Python import 跨 module boundary 传播。decorator 在 loader context 外执行仍然 fail closed。
- 显式 `model="other"` 永远覆盖 default，并触发组织校验；对象不会因为文件移动而静默改名。
- 文件系统路径只用于发现候选 Python 文件和做组织校验；对象身份只来自显式 `model=` 或显式 default model。
- loader 采用 two-pass 语义：第一阶段 collect 所有声明，第二阶段 resolve refs 和校验依赖。文件名和 sibling sort order 不应影响合法模型是否能加载。
- Python 文件是受信任本地代码，不做 sandbox。
- 成功加载后 registry 进入 `ready`；失败时清空部分模型，进入 `errored`，并记录结构化 `load_errors`。

文件组织应优先服务 agent 的增量修改。按类型拆成 `datasets.py` / `fields.py` / `metrics.py` 是可接受的组织方式，但不是前置要求；更推荐把一组相关业务对象放在同一个 feature-oriented 文件里，例如 `revenue_metrics.py` 同时包含 revenue 相关 dataset、field 和 metric。

## Reader / Introspection

Reader 层让 agent 和 `analysis_py` 读取明确的 `SemanticProject`，而不是重新解析文件或依赖进程全局状态：

```python
import marivo.semantic_py as ms

project = ms.find_project()
if project is None:
    raise SystemExit("No .marivo/semantic project found")

print(project.list_models())
print(project.search("revenue"))
print(project.dependencies("sales.revenue"))
print(project.describe("sales.revenue", compile_sql=True))
```

目标态 reader / introspection surface 以 project methods 为主：

| API | 语义 |
| --- | --- |
| `ms.find_project(start_dir=".")` | 从 `start_dir` 向上查找最近的 `.marivo/semantic/`，找到则返回 `SemanticProject`，否则返回 `None` |
| `project.list_models()` | 列出已加载 model |
| `project.list_datasources()` | 列出 `model.datasource` |
| `project.list_datasets(model=None)` | 列出 dataset，可按 model 过滤 |
| `project.list_metrics(dataset=None, decomposition=None, provenance_status=None)` | 列出 metric，可按 dataset、decomposition 或可信状态过滤 |
| `project.search(query, kind=None)` | 确定性搜索对象 |
| `project.dependencies(name)` | 返回某个对象的上游 datasource / dataset / field / metric / relationship 依赖图 |
| `project.dependents(name)` | 返回依赖某个对象的下游对象，供 agent 判断修改影响面 |
| `project.describe(name, compile_sql=False, format="object")` | 读取 datasource / dataset / metric 的结构化摘要，可选择编译 SQL |
| `project.materialize_dataset(name, backend_factory=...)` | 物化 dataset 到 Ibis table |
| `project.materialize_field(name, backend_factory=...)` | 物化 field 到 Ibis expression |
| `project.materialize_metric(name, backend_factory=...)` | 物化 metric 到 Ibis expression |
| `project.reload()` | 重新加载该项目 |

`ms.help(symbol=None)` 是模块级帮助 helper，独立于 `SemanticProject` 实例使用，不需要 active project；用于 REPL / agent 自我发现 API 形态。

`find_project()` 的 project 判定只要求 `.marivo/semantic/` 目录存在。空目录也算语义项目：`SemanticProject` 可返回，load 后 registry 为 `ready`，`list_models()` 返回 `[]`。如果 `.marivo/semantic` 存在但不是目录，必须 fail closed。

`project.search(query, kind=None)` 不做 embedding 或语义相似度匹配。它在 `semantic_id`、`name`、`description`、`ai_context.business_definition`、`ai_context.synonyms` 和 `ai_context.examples` 上做大小写不敏感的子串匹配；结果按字段优先级、semantic id 字典序稳定排序。这样 agent 可以写可预测断言，而不是依赖不可复现的模糊召回。

`describe(..., format="object")` 返回结构化 dataclass / dict，而不是只打印文本。最小字段包括 `semantic_id`、`kind`、`model`、`description`、`business_definition`、`guardrails`、`parity_status`、`compiled_sql`、`compile_error`、`source_sql`、`dependencies`、`dependents`、`python_symbol` 和 `source_location`。`format="text"` 只作为人类阅读糖；agent 默认消费结构化对象。

free function 形态只允许作为 REPL 糖保留；如果没有显式 active project，必须 fail closed，不能 silent fallback 到 CWD 推断。

## Materialization

Materialization 层把已注册的 Python 函数重新组合成 Ibis 对象。调用方提供 `backend_factory(datasource_name)`，语义层不自己构造连接：

```python
import marivo.semantic_py as ms

project = ms.SemanticProject(root=".marivo/semantic")
expr = project.materialize_metric(
    "sales.revenue",
    backend_factory=lambda datasource_name: con,
)
value = expr.execute()
```

目标态上，materialization 是 `analysis_py` 和测试工具进入真实数据执行的唯一通道。它不应绕过 registry，也不应从旧 OSI/SQLite runtime 反查定义。

`describe(..., compile_sql=True)` 应能在不执行查询的情况下返回 Ibis repr、backend-compiled SQL、`source_sql` 和 parity status，帮助 agent 调试口径差异。编译契约：

- compile target 默认来自 metric 依赖 datasource 的 `backend_type`。
- 如果传入 backend/compiler factory，实际 backend dialect 必须与声明的 `backend_type` 一致，否则 fail closed。
- 无 backend_factory 时，系统应使用 `backend_type` 对应的 dry compiler；若该 backend type 没有可用 compiler，返回结构化 `compile_error`，而不是执行查询。
- 多 datasource metric 在 compile 和 parity 中默认 fail closed；后续 federation 需要单独设计。
- 编译失败返回 `compiled_sql=null`、`compile_error={kind,message,refs}`；`strict=True` 时可 raise。

## 核心对象模型

### SemanticProject

目标态：`SemanticProject` 是唯一显式项目边界；reader、reload、materialization 都优先通过 project methods 调用。

```python
from marivo.semantic_py import SemanticProject

project = SemanticProject(root="/path/to/.marivo/semantic")
```

它拥有独立 registry 和加载锁。目标态上，一个 `analysis_py` session 应显式绑定到项目 root 下的语义项目，避免在不同 CWD 或不同 checkout 间误读模型。

### Model

model 是业务域边界，例如 `sales`、`marketing`、`subscription`。model 名称参与下游 semantic id，例如 `sales.revenue`。agent 不应用自然语言近似匹配替代 model id；如果不确定，应先 `project.list_models()` / `project.describe(...)`。

每个 model 目录可维护 `_exports.py`，统一 re-export 该 model 允许其他 model 引用的 decorated objects。`_exports.py` 不声明新对象，只做 re-export。它是推荐 convention，不是强制 contract。

```python
# .marivo/semantic/marketing/_exports.py
from .datasets import sessions_daily, users
from .fields import user_id
from .metrics import sessions, signups
```

跨 model 引用时，如果被引用 model 有 `_exports.py`，应优先从 `_exports.py` import；如果没有，可直接从对应 sibling file import decorated ref，或使用字符串 `ms.ref(...)`。check 应把跨 model 直接 import 标为 hint 级提示，建议被引用方补 `_exports.py`。

### Datasource

Datasource 是项目级配置，不属于任何 semantic model。它定义在 `.marivo/datasource/*.py`，可随 `.marivo/semantic` 一起复制到其他分析项目复用。

```python
import marivo.datasource_py as md

md.datasource(
    name="warehouse",
    backend_type="trino",
    host="trino.example.com",
    port=8080,
    catalog="hive",
    schema="default",
    user_env="WAREHOUSE_USER",
    password_env="WAREHOUSE_PASSWORD",
)
```

设计约束：

- datasource name 是全局 key，禁止使用 `<model>.<datasource>`。
- semantic model 不调用 `ms.datasource(...)`，只在 `@ms.dataset(datasource="warehouse")` 中引用全局 datasource name。
- 非机密连接字段写在 datasource 文件里；`user`、`password`、`token`、`api_key`、`secret`、`private_key` 等机密字段只能通过 `<field>_env` 引用环境变量。
- datasource 是 dataset 的执行来源，不是 metric 的业务口径。

### Dataset

dataset 是业务实体或事实表的逻辑视图：

```python
from marivo.semantic_py.typing import IbisBackend

@ms.dataset(
    model="sales",
    name="orders",
    datasource=warehouse,
    primary_key=["order_id"],
    description="Order facts.",
)
def orders(backend: IbisBackend):
    return backend.table("orders")
```

dataset 函数返回 Ibis table。它可以封装物理表名、必要的基础投影或稳定的源级过滤，但不应把 metric 聚合逻辑塞进 dataset。

dataset body 允许受限使用 `backend.sql(...)` 封装 SQL view，因为实际项目常把稳定物理视图写成 SQL。限制如下：

- `describe` 必须显示 `dataset_provenance="sql_view"` 和 SQL text / source location。
- 依赖 SQL-view dataset 的 metric 仍可 materialize，但 parity status 必须显示底层 dataset 含 SQL view。
- parity 工具默认拒绝把 SQL-view dataset 当作“纯 Ibis 翻译”进行同源 SQL parity；需要显式 fixture-based parity。
- 若 SQL view 已在后端持久化为表/视图，优先用 `backend.table(...)` 暴露，减少 Python 语义层内嵌 SQL。

### Field 和 Time Field

field 是 row-level 属性，供过滤、分组、relationship 或 metric 表达式复用：

```python
@ms.field(model="sales", dataset=orders, description="Normalized region.")
def region(orders):
    return orders.region.upper()
```

time field 是特殊 field，显式承载时间轴元数据：

```python
@ms.time_field(
    model="sales",
    dataset=orders,
    data_type="date",
    granularity="day",
    format=None,
    description="Order creation date.",
)
def order_date(orders):
    return orders.created_at.cast("date")
```

设计约束：

- 需要作为时间窗口、时间粒度或 calendar axis 使用的字段必须声明为 `time_field`。
- 普通 `field` 不应靠名称如 `dt`、`date`、`event_time` 被自动推断为时间字段。
- `data_type` 支持 `date`、`datetime`、`timestamp`、`string`、`integer`；字符串或整数时间字段用可选 `format` 声明物理格式。
- hour-only 字段（例如 `data_type="string", format="hh"` 或 `data_type="integer", format="h"`）必须显式声明 `required_prefix`；timestamp/datetime hour 字段或单列完整 hour 格式不需要。
- 若 metric body 内出现 `.filter(...)`、`.cast(...)` 或多步链式 row-level 中间表达式，且该表达式代表可命名业务概念，应先抽成 `field` / `time_field`，再在 metric 中引用。
- `@ms.field` / `@ms.time_field` 不要求 provenance status。它们的可信度来自所属 dataset、row-level 表达式可读性和 materialization 校验。`source_sql` 是可选审计字段；缺失时 `describe` 显示 `provenance=null`。

### Metric

目标态统一使用 `@ms.metric(...)`。`datasets=[...]` 非空时是 base metric；省略 `datasets` 且 decomposition 带 components 时是 derived metric。

```python
@ms.metric(
    model="sales",
    datasets=[orders],
    decomposition=ms.sum(),
    description="Total revenue from paid orders.",
    source_sql="select sum(amount) as value from orders where pay_status = 1",
    source_dialect="duckdb",
    source_document="kb://sales/revenue",
)
def revenue(order_rows):
    return order_rows.filter(is_paid(order_rows)).amount.sum()
```

base metric 使用 `datasets=[...]` 显式声明依赖。函数 body 的参数只是局部 alias，按 `datasets` 顺序注入 materialized table；参数名不能决定 dataset identity。

纯派生 metric 不应强制声明无用 `datasets=[...]`。目标态仍使用 `@ms.metric`，components 的 dataset 依赖联合构成该 metric 的隐含依赖：

```python
@ms.metric(
    model="sales",
    decomposition=ms.ratio(
        numerator=converted_users,
        denominator=total_users,
    ),
    provenance="python_native",
)
def conversion_rate():
    return ms.component("numerator") / ms.component("denominator")
```

形态判定必须 fail closed：

- `datasets=[...]` 非空，body 使用 dataset aliases：base metric。
- `datasets` 省略或为空，decomposition components 非空，body 只使用 `ms.component("<name>")`、数字字面量和允许的算术运算：derived metric。
- `decomposition=ms.sum()` 没有 components，因此必须是 base metric；省略 `datasets=[...]` 时直接报 `missing_datasets`。
- `datasets` 和 component-only body 同时出现：错误。
- 没有 `datasets` 且没有 decomposition components：错误。

### Relationship

relationship 描述 dataset 之间的连接路径：

```python
# .marivo/semantic/sales/relationships.py
import marivo.semantic_py as ms
from .datasets import orders
from .fields import order_user_id
from marketing._exports import users, user_id

ms.relationship(
    name="orders_to_users",
    from_dataset=orders,
    to_dataset=users,
    from_fields=[order_user_id],
    to_fields=[user_id],
)
```

目标态 relationship 是纯 metadata 顶级调用。连接键必须使用 `field` / `time_field` 的 ref 引用，不能使用裸字符串物理列名。`from_columns` / `to_columns` 不应作为 alias 继续保留；目标态只接受 `from_fields` / `to_fields`，值为 decorated field refs 或 `ms.ref("field.<model>.<dataset>.<field>")`。

### Decomposition

decomposition 描述 metric 在变化归因中的数学结构，不等同于 SQL aggregation：

| Builder | 适用 metric | 组件要求 |
| --- | --- | --- |
| `ms.sum()` | 可加总数量，如 revenue、orders、users | 无组件 |
| `ms.ratio(numerator=..., denominator=...)` | 比例/转化率，如 conversion_rate | numerator 和 denominator 都是 metric ref |
| `ms.weighted_average(value=..., weight=...)` | ratio-of-sums 或带权均值，如 ARPU | numerator 和 weight 都是 metric ref |

目标态禁止在 metric body 内直接调用 decorated metric 函数来表达派生 metric。派生 metric body 应使用 `ms.component("numerator")`、`ms.component("denominator")`、`ms.component("weight")` 这类显式 sentinel call。

`ms.component("<name>")` 只允许出现在 derived metric 函数体 AST 内。模块顶层调用或 base metric 函数体中调用必须 fail closed。`<name>` 必须是字符串字面量，并且必须匹配 decomposition builder 上声明的 component name。它的返回类型应暴露为 Ibis scalar expression Protocol，便于 type checker 和 agent 做算术表达式检查。

`ms.help("component")` 应展示当前支持的 component names、可用算术和禁止形态，避免 agent 通过猜测 `numerator` / `denominator` / `weight` 的名称来 author derived metric。

`ms.component("numerator")` / `ms.component("denominator")` / `ms.component("weight")` 在 decorator-time 返回 deferred Ibis expression sentinel。sentinel 支持 `+`、`-`、`*`、`/` 和一元 `-`；运算结果仍是 deferred sentinel tree。materialize-time 将 sentinel tree 的 leaves 替换为真实 component metric 的 Ibis scalar，再编译到目标 backend。

`ms.component(...)` 的返回类型在 `marivo.semantic_py.typing` 中导出为 `ComponentExpr`（确定名称由实现期 freeze）。agent 通常无需显式标注；mypy / Pyright 会自动推断算术结果仍为 `ComponentExpr`。

derived metric 不在 Python 层做零除保护；materialize 后的 Ibis 表达式在目标 backend 中按 SQL 语义处理，多数 backend 在分母为 0 时返回 `NULL`。需要明确 fallback（例如返回 0、跳过该 slice）时，应把保护逻辑封装到 base metric 内，再作为 component 引用，而不是在 derived metric body 内尝试条件表达式——白名单不会接受。

Derived metric body AST 白名单：

| 形态 | 是否允许 |
| --- | --- |
| `ms.component("<component_name>")` 字符串字面量调用 | 允许 |
| 数值字面量、`None` | 允许 |
| 二元 `+`、`-`、`*`、`/` 和一元 `-` | 允许 |
| 括号 | 允许 |
| `abs(...)`、`ms.literal(...)`、除 `ms.component(...)` 外的任意函数调用 | 禁止 |
| `.xxx` attribute access | 禁止 |
| subscription、comparison、boolean op、conditional expression、字符串字面量 | 禁止 |
| dataset、field、time_field 或任何非 component 对象引用 | 禁止 |

例外：`ms.component(...)` 调用的唯一位置参数允许且必须是字符串字面量，且必须等于当前 metric decomposition builder 声明的 component name 之一。

如果派生计算需要 field/time_field 或 row-level 中间值，先把它封装成 base metric，再把该 base metric 作为 decomposition component。Derived metric 不能直接引用 dataset、field 或 time_field。

### Provenance

目标态 metric 始终有 provenance status，但 authoring-time 不强迫 agent 先完成 SQL 溯源。缺省状态是 `unverified`；当 metric 被提升为正式分析口径、被 `analysis_py.observe()` 消费、进入 strict CI，或声明为可信业务对象时，必须显式选择 SQL triple 或 `python_native`：

| Provenance | 含义 |
| --- | --- |
| `source_sql` + `source_dialect` + `source_document` | 从 SQL / BI / 知识库迁移，必须可做 parity |
| `provenance="python_native"` | Python/Ibis 是唯一业务源头，没有上游 SQL oracle |
| 省略 provenance 或 `provenance="unverified"` | 临时定义，允许加载但在 describe、summary、analysis frame 中显式标红 |

`source_sql` 是单 dialect provenance。若同一 metric 需要多 dialect 验证，不应把多份 SQL 都塞进 decorator；应使用 fixture-based parity tests 或后续 parity fixture lifecycle 来覆盖额外 dialect。

缺少 source SQL 不等于错误；缺少显式 provenance 参数也不阻塞 authoring-time 加载，但该 metric 的 status 必须被标为 `unverified`。agent 和下游 analysis frame 必须能看到该 metric 是 `verified`、`python_native`、`unverified` 还是 `drifted`。当 check 使用 `--strict-provenance`、metric 被正式 analysis workflow 消费，或项目策略要求可信对象时，`unverified` 必须导致 fail closed。

Derived metric 的有效 parity status 同时受自身 provenance 和 component statuses 约束：

- 自身有独立 SQL oracle 且 parity 通过、所有 components 都 `verified`，结果为 `verified`。
- 自身声明 `python_native` 时，不覆盖 components 的弱状态；所有 components `verified` / `python_native` 时可暴露为 `python_native`，任一 component `unverified` 则结果为 `unverified`。
- 任一 component 为 `drifted`，derived metric 结果为 `drifted`。
- 自身 `unverified` 或任一 component `unverified`，结果为 `unverified`，除非已有更弱的 `drifted`。

## Agent 工作流

### 1. 先读取现状

agent 在新增或修改语义前应先运行确定性的 check 或读取当前 registry。目标态首选 fresh-process CLI：

```bash
.venv/bin/python -m marivo.semantic_py check --project .marivo/semantic
```

`check` 命令要求：

- 缺省向上查找最近的 `.marivo/semantic/`，找不到时 fail closed 并提示 `--project`。
- 使用 fresh interpreter 加载项目，避免 namespace package 和模块缓存影响修复循环。
- 打印所有 decorator / load / assembly errors，包含结构化 kind、refs、location、hint 和人类可读摘要。
- 非零退出码表示存在未解决错误。
- 可选 `--parity` 对所有声明了 `source_sql` 的 metric 运行 parity。
- 可选 `--strict-provenance` 将任何 `unverified` metric 视为非零退出。检查 metric 自身 provenance status 和 derived metric 的传播 status；任一非 `verified` / `python_native` 都触发。例如 derived metric 自身已 `python_native` 但某个 component 仍 `unverified` 时同样退出，避免 agent 误以为"提升自己就够了"。
- 默认列出所有字符串 refs 和 unverified metrics，作为 agent 需要复核的 warning。
- 支持 `--format=json` 输出结构化 errors / warnings / refs / parity statuses，便于 agent 稳定解析。

需要探索对象时，再用项目显式 API。agent 进入一个新 repo 后的默认入口是 `ms.find_project()`；找不到时不要猜 root，应提示初始化或显式传入 `--project`。

### 2. 声明最小业务对象

新增 metric 时的最小 happy path 是 datasource、dataset、metric 和 decomposition。只有当分析需要时间窗口、过滤复用或跨表关系时，再渐进加入 time_field、field 和 relationship。

新建 metric 可以省略 provenance 并自动进入 `unverified`，但 agent 不能把它当作完成状态。若同一 PR 新增多个 unverified metrics，应停下来确认业务来源；CI 可用 `--strict-provenance` 禁止 unverified metric 合入。

### 3. Reload 并处理结构化错误

修改 authoring 文件后，应优先运行 `check`。REPL 中可调用 `project.reload()`，但 agent fix loop 不应依赖 thread-local active project 或上一次 import 的模块缓存。遇到 `SemanticDecoratorError`、`SemanticLoadError`、`SemanticRuntimeError`、`SemanticParityError` 时，优先按错误中的 kind、refs、hint 和 source location 修改定义，不要用 try/except 隐藏错误。

### 4. Materialize 或交给 analysis_py

语义层自身只产出 Ibis object。实际分析应由 `analysis_py` operator 或上层 session 执行：

```python
import marivo.analysis_py as mv

session = mv.session.get_or_create(name="revenue-investigation")
frame = mv.observe(mv.MetricRef("sales.revenue"), session=session)
print(frame.summary())
```

目标态边界是：`semantic_py` 负责“对象是什么、口径是什么、如何物化”；`analysis_py` 负责“对这些对象执行 observe/compare/decompose/detect/correlate 等分析步骤并持久化 artifact/lineage”。

## Agent 决策规则

### Field vs Metric

| 问题 | 选择 |
| --- | --- |
| 每一行都能计算出来，例如国家、平台、订单日期 | `@ms.field` 或 `@ms.time_field` |
| 需要跨行聚合，例如 revenue、DAU、conversion rate | `@ms.metric` |
| 只是 metric 内部的一段条件表达式，不需要下游引用 | 可直接写在 metric Ibis 表达式内 |
| 会被多个 metric、filter、relationship 或分析 slice 复用 | 提升为 field/time_field |

为了让 agent 能机械执行，目标态再加一条硬规则：metric body 内只允许聚合表达式和对已声明 field/time_field 的引用。凡是 row-level `.filter(...)`、`.cast(...)`、复杂 `case`、多步链式中间值，默认先抽成 `field` 或 `time_field`；只有一次性且无业务命名价值的简单列访问可以留在 metric body。

### Sum vs Ratio vs Weighted Average

| Metric 形态 | Decomposition |
| --- | --- |
| 可直接加总的绝对量 | `ms.sum()` |
| `numerator / denominator`，如转化率、成功率 | `ms.ratio(numerator=..., denominator=...)` |
| 分段均值需要权重解释 mix effect | `ms.weighted_average(value=..., weight=...)` |

如果 agent 不确定 decomposition，不能随便填 `ms.sum()`。应先从业务定义、source SQL、已有 metric components 或用户确认中确定结构。

### 什么时候使用 ref

目标态优先使用 decorated object refs，因为它能让 Python 静态阅读和重构更直接。跨 model 引用也应优先通过被引用 model 的边界文件 re-export 成 Python 符号，再在引用方导入 decorated ref。字符串 `ms.ref(...)` 只用于无法自然 import 的前向引用或工具生成场景。

```python
from marketing._exports import sessions

@ms.metric(
    model="sales",
    decomposition=ms.ratio(
        numerator=sessions,
        denominator=total_users,
    ),
)
def sessions_per_user():
    return ms.component("numerator") / ms.component("denominator")
```

`ms.ref(...)` 的唯一位置参数格式固定为 `"<kind>.<fully-qualified-id>"`。`kind` 取值：`datasource`、`dataset`、`field`、`time_field`、`metric`、`relationship`。例：

- `ms.ref("metric.marketing.sessions")`
- `ms.ref("field.sales.orders.user_id")`
- `ms.ref("dataset.marketing.sessions_daily")`

跨 model refs 允许，但必须在 resolve 阶段做存在性、cycle 和 contract 检查；不能退回到 SQL provenance 里复制另一个 model 的定义。

因为字符串 ref 是重构风险，`check` 默认应列出所有字符串 refs，并标记为 `potentially_fragile_reference`。目标态还应提供结构化重命名工具，例如 `.venv/bin/python -m marivo.semantic_py refactor rename metric old new`，让 agent 优先通过工具修改 semantic refs，而不是手工 grep。

Refactor 工具契约：

```bash
.venv/bin/python -m marivo.semantic_py refactor rename metric sales.old_revenue sales.revenue
.venv/bin/python -m marivo.semantic_py refactor rename field sales.orders.old_user_id sales.orders.user_id --write
```

- 缺省 dry-run，输出 unified diff 和变更文件列表；只有 `--write` 才落盘。
- 输入是 `<kind> <old-fqn> <new-fqn>`，`kind` 至少覆盖 `model`、`datasource`、`dataset`、`field`、`time_field`、`metric`、`relationship`。
- 覆盖范围包括 decorator / metadata call 的 `name=`、`model=` 必要改动、`ms.ref(...)` 字符串、relationship endpoint refs、`from_fields` / `to_fields`、decomposition component refs、`_exports.py` re-export。
- 不修改 `source_sql`、`source_document` 或自然语言 prose；这些字段需要人工 review。

## 验证与失败语义

Python 语义层使用多层 fail-closed 验证。

### Decorator-time

decorator 执行时检查局部声明是否自洽：

- model/datasource/dataset/field/metric 重名。
- decorated ref 类型错误。
- 跨 model / 跨 dataset ref 不合法。
- expression-bearing decorator 缺少显式 `model=`，且所在加载上下文没有显式 default model。此错误只在 `_model.py` 显式 `default=False`，或对象声明在 model 目录之外的文件中时触发；缺省 `default=True` 场景下，同目录对象自然继承 model，不会进入此错误路径。
- base metric 缺少 `datasets=[...]`。
- derived metric 带 dataset 参数、缺少 decomposition components 或在 body 中读取 dataset table。
- decorator / metadata call 出现在 semantic loader context 之外。
- metric 函数体不满足单 return 表达式约束。
- metric body 调用 decorated metric 函数，derived metric body 调用 `ms.component(...)` 之外的函数，或使用 Ibis SQL escape hatch。

`outside_loader_context` 错误必须带可执行 hint：把定义移动到 `<project_root>/.marivo/semantic/<model>/<file>.py`，然后运行 `.venv/bin/python -m marivo.semantic_py check --project <project_root>/.marivo/semantic`；如果是在 notebook 中探索，使用 scratch Ibis expressions，只有注册对象才走 semantic loader。

常见 structured error 到 agent action 的映射应足够机械：

| Error kind | Agent action |
| --- | --- |
| `duplicate_name` | 检查同一 model 内是否重复声明；删除旧声明或改 `name=`，再运行 check |
| `missing_model` | 在 `<root>/<model>/_model.py` 增加 `ms.model(name=...)`，或在对象声明上补 `model=` |
| `missing_dataset_ref` | 确认 dataset 已声明；若跨文件前向引用，改用 decorated ref 或 `ms.ref(...)` |
| `cross_model_reference` | 优先通过 `_exports.py` 导入；没有 `_exports.py` 时可直接 import sibling file 或使用显式 `ms.ref(...)` |
| `invalid_decomposition` | 检查 `ms.ratio(...)` / `ms.weighted_average(...)` 的 components 是否都指向已注册 metric |
| `invalid_component_body` | derived metric body 只保留 `ms.component("<name>")`、数字字面量和允许的算术 |
| `outside_loader_context` | 把定义移到 `<root>/.marivo/semantic/<model>/<file>.py`；notebook 探索改用 scratch Ibis 表达式 |
| `unverified_provenance` | 若要进入 strict workflow，补 `source_sql` triple、改为 `python_native`，或先停止并确认业务口径 |
| `sql_escape_hatch` | 把 raw SQL 移到 dataset SQL view 或后端持久视图；metric body 保持 Ibis expression |

### Load / Assembly-time

loader 执行项目文件后，assembly validation 检查跨对象关系：

- `_model.py` 缺失或 model 注册不匹配目录。
- `ms.model(...)` 出现在非 `<root>/<model>/_model.py` 文件，或一个 `_model.py` 声明多个 model。
- dataset 引用不存在的 datasource。
- metric 引用不存在的 dataset 或 decomposition component。
- cross-model `ms.ref(...)` 不存在、kind 不匹配或形成循环依赖。
- `datasets=[...]` 注入顺序与 metric 函数参数数量不一致。
- hour time field 缺少 required prefix。
- relationship endpoint、join field refs、field dataset membership 或 arity 不合法。

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

- dataset primary key metadata 可选开启 sample uniqueness check；默认不阻塞加载，但 check 输出应把未验证 PK 标为 warning。
- metric body 禁止 `backend.sql(...)`、Ibis raw SQL escape hatch 或 dialect-specific SQL snippets。跨 dialect 的 vendor 差异应通过 datasource/backend compilation 和 parity 暴露，而不是藏在 metric body。
- SQL escape hatch 检查在 materialize-time 扫描 Ibis expression tree 中的 raw SQL node；decorator-time 只做显式 `backend.sql(...)`、`.raw_sql(...)` 等明显方法名的早期拒绝，避免仅靠 AST 误伤普通列名。

## 与 OSI-Marivo 的关系

OSI-Marivo spec 提供了重要语义参考：semantic model、dataset、field、relationship、metric、time granularity、AI context 和 MARIVO decomposition extensions。Python 语义层借鉴这些对象边界，但不被旧链路约束。

本文档采用以下边界：

- Python 文件是 Python-native track 的 source of truth。
- OSI YAML/JSON、SQLite metadata store 和旧 runtime 是独立 track，不是本文档要求的兼容目标。
- 本文档不承诺 Python 定义与 OSI 文档双向转换。
- 本文档不要求把 Python semantic definitions 持久化到 SQLite。
- OSI 的对象语义可作为命名、decomposition、time-field metadata 的参考，但 Python API 可以为 agent ergonomics 做不同取舍。

## 与 analysis_py 的关系

`semantic_py` 和 `analysis_py` 是 Python-native Marivo 的两段式架构：

```text
semantic_py:  datasource / dataset / field / metric / relationship
      ↓
Ibis materialization + typed semantic refs
      ↓
analysis_py: observe / compare / decompose / detect / correlate / ...
      ↓
typed frames + session persistence + lineage
```

设计边界：

- `semantic_py` 不产出 `MetricFrame`、`DeltaFrame` 或 attribution artifact。
- `analysis_py` 不重新定义 metric 口径，不猜 dataset/time field，不绕过 semantic registry 直接读表。
- backend ownership 位于 profile/session/execution 层；semantic object 只声明
  datasource 名称引用，不声明 backend type 或连接字段。
- 下游 analysis operator 应通过 semantic refs 读取对象，例如 `sales.revenue`，并通过 materialization 获得 Ibis expression。

如果一个分析需要新的业务对象，应先扩展 `semantic_py`，再让 `analysis_py` 消费它；不应把业务定义隐藏在一次性 analysis script 中。

## v1 已落地边界

当前 `marivo/semantic_py` 已经提供以下能力：

- `SemanticProject`、project-scoped registry、context-local active registry。
- decorators：`model`、`datasource`、`dataset`、`field`、`time_field`、`metric`、`relationship`。
- builders：`sum`、`ratio`、`weighted_average`、`ref`。
- loader：model 目录扫描、`_model.py` 优先执行、sibling files 排序执行、reload 清理项目模块。
- reader/introspection：`list_models`、`list_datasources`、`list_datasets`、`list_metrics`、`describe`、`help`、`reload`。v1 现状的 reader 是 module-level free functions，依赖 context-local active project；v1.1 全部迁移到 `SemanticProject` methods（参见上方 §Reader / Introspection），free function 形态仅作为有显式 active project 的 REPL 糖保留。
- materialization：dataset、field、metric 到 Ibis object。
- validation：metric body AST 约束、missing refs、time prefix、relationship endpoint/columns/arity。
- SQL provenance：`source_sql`、`source_dialect`、`source_document`、`source_notes`。
- parity helper：`compare_metric_to_source_sql` 和 `ParityResult`。
- structured errors：decorator、assembly、runtime、parity、load errors。

当前 v1 仍保留若干 agent footguns：model 归属部分依赖 loader 上下文，metric dataset 依赖来自函数参数名，datasource / relationship 是 decorator body 形态，reader 主要是 free functions，provenance 不是必填状态，loader 行为仍暴露 sibling file sort order。这些是目标态要移除的兼容负担，不应继续固化为长期设计。

## v1.1 Breaking 目标

以下目标应作为下一轮实现的破坏性契约调整，而不是长期后续愿望：

- 所有语义对象使用显式 `model=` 或显式 default model；文件位置只做 discovery 和组织校验。
- `ms.model(...)` 只能出现在 `<root>/<model>/_model.py`，且 `name` 必须等于目录名；`default` 缺省为 `True`，允许同目录对象省略重复 `model=`。
- `_model.py` 可作为 single-file quick path；对象变多后推荐拆为 feature-oriented sibling files。
- Metric 显式 `datasets=[...]`；函数参数名只做局部 alias。
- Base 和 derived metric 统一使用 `@ms.metric`；derived metric 不要求 `datasets=[...]`，依赖来自 components。
- Decomposition component placeholder 使用 `ms.component("<name>")` sentinel call。
- Derived metric body 使用明确 AST 白名单；禁止 `ms.component(...)` 之外的函数调用、attribute chaining、comparison、dataset/field/time_field 引用。
- Derived metric 的有效 parity status 从自身 provenance 和 components status 中取更弱者。
- Datasource 和 relationship 改为顶级 metadata call，不再要求无意义 function body。
- Relationship join keys 改为 field/time_field refs；裸字符串 `from_columns` / `to_columns` 不再是目标态契约。
- Reader / materialization 主 API 迁移到 `SemanticProject` methods；free functions 只保留为有显式 active project 的 REPL sugar。
- Metric provenance status 始终存在；authoring-time 缺省为 `unverified`，promotion / strict CI / analysis consumption 前必须提升为 SQL triple 或 `python_native`。
- Parity status 成为 metric / frame / describe 的可见属性。
- Field / time_field 不要求 provenance status；缺失 provenance 在 describe 中显示为 `null`。
- Dataset SQL view 允许但必须显式标记 `dataset_provenance="sql_view"`，且 parity 默认要求 fixture-based 验证。
- 提供 `.venv/bin/python -m marivo.semantic_py check --project ...` fresh-process check 命令。
- `check` 缺省向上查找 `.marivo/semantic/`，支持 `--strict-provenance`，并默认提示字符串 refs。
- 提供 semantic refactor rename 工具，减少 agent 手工重命名字符串 refs。
- Loader 采用 two-pass collect / resolve；合法 ref 不受 sibling filename sort order 影响。
- `find_project()` 向上查找 `.marivo/semantic/`，作为 agent 进入新 repo 的第一步。
- Reader 增加 `search`、`dependencies`、`dependents` 和更丰富的 `list_metrics` 过滤。
- `describe(..., compile_sql=True)` 返回结构化对象，包含 Ibis repr、compiled SQL、source SQL、dependencies、source location 和 parity status；`format="text"` 只作阅读糖。
- `name=` 是 semantic identity；Python 符号名只是 local alias，check/describe 显示二者映射。
- structured errors 提供 error kind 到 agent action 的稳定映射。
- `ai_context` 收敛为固定 schema，适用于所有语义对象，至少包含 `business_definition` 和 `guardrails`；禁止不可消费的自由 dict。

## 后续演进

以下是 v1.1 之后的方向：

- 更完整的 relationship-aware materialization 和 cross-dataset filter resolution。
- dataset primary / unique key 的可配置 sample validation。
- 更丰富的 generated SQL diff 和 parity fixture lifecycle。
- 明确 Ibis 跨 dialect 表达式失败的分类：普通 Ibis 表达式编译失败、raw SQL escape、backend capability gap 应返回不同 structured error。
- 面向 agent 的例子库和 skill 文档，与 public API drift check 绑定。

新增演进必须保持一个原则：不要引入与 Python authoring source of truth 冲突的第二套业务口径定义，也不要把隐式推断重新包装成 agent ergonomic shortcut。

## 测试与维护

修改 Python 语义层行为后，应使用仓库 entrypoints 验证。针对当前 semantic_py 的常用聚焦命令是：

```bash
make test TESTS=tests/test_semantic_py_decorators.py
make test TESTS=tests/test_semantic_py_materialization.py
make test TESTS=tests/test_semantic_py_parity.py
```

文档-only 修改至少应运行：

```bash
git diff --check -- docs/specs/semantic/python-semantic-layer.md
```

维护规则：

- 文档里的 current-state API 必须与 `marivo.semantic_py` 公开导出对齐。
- 示例不能使用 bare `python`、`pytest`、`mypy` 或 `ruff`。
- 目标态能力必须明确标注为目标态或后续演进。
- 不要把旧 OSI/SQLite/runtime track 的兼容性承诺写进 Python-native 设计文档。
