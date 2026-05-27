# marivo.semantic_py 重构设计（v1.1 严格落地）

状态：design spec，待批准。本文锁定按 [`docs/specs/semantic/python-semantic-layer.md`](../../specs/semantic/python-semantic-layer.md) v1.1 设计严格重写 `marivo/semantic_py/` 的实现契约。不考虑 v1 兼容、不留迁移 shim、不保留 free-function reader。

## 1. 范围与约束

### 1.1 在本轮范围内

- `marivo/semantic_py/` 全量原地重写到 v1.1 公开 surface。
- 同 PR 更新 3 处 `analysis_py` 衔接点 + 6 个 skill examples + 1 个 cross-skill fixture。
- 同 PR 替换 11 个 `tests/test_semantic_py_*.py` 与 6 个 `tests/test_analysis_py_*.py`，按 v1.1 surface 重写。
- 设计文档 v1.1 全部 core 行为：authoring + loader + validator + reader + materializer + parity + ai_context schema + parity status 传播 + `check` CLI。

### 1.2 不在本轮范围（follow-up PR）

- `python -m marivo.semantic_py refactor rename` CLI 实现（留 stub，不暴露 `--help`）。
- `analysis_py.observe` 改用 `project.describe()` 拿 metric metadata（仅替换 source，行为不变）。
- parity fixture lifecycle、generated SQL diff、relationship-aware materialization。

### 1.3 硬性约束

- 严格 follow 设计文档；不考虑 v1 兼容和迁移。
- 原地替换 `marivo/semantic_py/`，一次性 breaking。
- TDD：每个 v1.1 行为先写 failing test 再实现。
- 测试唯一入口：`tmp_path/.marivo/semantic/<model>/...py` + `SemanticProject(root).load()`。无后门 register API。

## 2. 模块布局与公开 API

### 2.1 文件结构（按生命周期阶段分模块，Approach B）

```
marivo/semantic_py/
  __init__.py          # 仅 re-export，公开 surface canonical
  typing.py            # IbisBackend Protocol, ComponentExpr Protocol, AiContext TypedDict
  errors.py            # 错误类层级 + ErrorKind enum
  ir.py                # 所有 IR dataclass（frozen，value semantics）
  authoring.py         # decorators + builders + ms.component + LoaderContext
  loader.py            # discovery, two-pass collect/resolve, find_project
  validator.py         # decorator-time + assembly-time + AST 白名单
  reader.py            # SemanticProject methods + structured Description
  materializer.py      # backend 实例化 + materialize + compile_sql
  parity.py            # SQL parity + parity status propagation
  help.py              # ms.help() module-level helper
  cli/
    __main__.py        # `python -m marivo.semantic_py`
    check.py           # `check` subcommand
```

### 2.2 `marivo.semantic_py.__init__.py`

```python
from .loader import find_project
from .reader import SemanticProject
from .authoring import (
    model, datasource, dataset, field, time_field, metric, relationship,
    sum, ratio, weighted_average, ref, component,
)
from .help import help
from . import typing as typing
from . import errors as errors

__all__ = [
    "SemanticProject", "find_project",
    "model", "datasource", "dataset", "field", "time_field",
    "metric", "relationship",
    "sum", "ratio", "weighted_average", "ref", "component",
    "help", "typing", "errors",
]
```

变化点 vs 当前 v1：

- 新增 `find_project`、`component`。
- 删除 module-level `list_*` / `describe` / `reload` —— loader-only authoring 模型下没有 active project 概念可用，free function reader 不再保留。
- `compare_metric_to_source_sql` 改为 `SemanticProject.parity_check(name, backend_factory=...)` method。

### 2.3 `marivo.semantic_py.typing`

```python
class IbisBackend(Protocol):
    def table(self, name: str, /) -> ibis.Table: ...
    def sql(self, query: str, /) -> ibis.Table: ...

class ComponentExpr(Protocol):
    def __add__(self, other: "ComponentExpr | int | float") -> "ComponentExpr": ...
    def __sub__(self, other: "ComponentExpr | int | float") -> "ComponentExpr": ...
    def __mul__(self, other: "ComponentExpr | int | float") -> "ComponentExpr": ...
    def __truediv__(self, other: "ComponentExpr | int | float") -> "ComponentExpr": ...
    def __neg__(self) -> "ComponentExpr": ...
    # 反向 op (__radd__, __rsub__, __rmul__, __rtruediv__) 同样暴露

class AiContext(TypedDict, total=False):
    business_definition: str | None
    guardrails: list[str]
    synonyms: list[str]
    examples: list[str]
    instructions: str | None
    owner_notes: str | None
```

### 2.4 CLI 入口

```
python -m marivo.semantic_py check [--project DIR] [--strict-provenance] [--parity] [--format=text|json]
```

`python -m` 启动是 fresh interpreter，不需自己 subprocess fork。

## 3. IR 设计

### 3.1 总则

- 所有 IR `dataclass(frozen=True)`，value semantics，可序列化（用于未来 `describe(format="object")` 持久化）。
- **callable 不进 IR**：loader collect 阶段把 `(IR, callable)` 存到 registry sidecar map，materializer 用 sidecar callable。
- semantic_id 形态固定：
  - datasource: `<model>.<name>`
  - dataset: `<model>.<name>`
  - field / time_field: `<model>.<dataset>.<name>`
  - metric: `<model>.<name>`
  - relationship: `<model>.<name>`

### 3.2 关键 enum

```python
class SymbolKind(str, Enum):
    MODEL = "model"
    DATASOURCE = "datasource"
    DATASET = "dataset"
    FIELD = "field"
    TIME_FIELD = "time_field"
    METRIC = "metric"
    RELATIONSHIP = "relationship"

class ParityStatus(str, Enum):
    VERIFIED = "verified"
    PYTHON_NATIVE = "python_native"
    UNVERIFIED = "unverified"
    DRIFTED = "drifted"

class DatasetProvenance(str, Enum):
    IBIS_TABLE = "ibis_table"
    SQL_VIEW = "sql_view"
```

### 3.3 IR dataclass

```python
@dataclass(frozen=True)
class SourceLocation:
    file: str         # absolute path
    line: int

@dataclass(frozen=True)
class AiContextIR:
    business_definition: str | None = None
    guardrails: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()
    instructions: str | None = None
    owner_notes: str | None = None

@dataclass(frozen=True)
class ProvenanceIR:
    source_sql: str | None = None
    source_dialect: str | None = None
    source_document: str | None = None
    source_notes: str | None = None
    declared_status: Literal["python_native", "unverified"] | None = None

@dataclass(frozen=True)
class ModelIR:
    name: str
    description: str | None
    default: bool
    ai_context: AiContextIR
    location: SourceLocation

@dataclass(frozen=True)
class DatasourceIR:
    semantic_id: str; model: str; name: str
    backend_type: str
    description: str | None
    ai_context: AiContextIR
    python_symbol: str
    location: SourceLocation

@dataclass(frozen=True)
class DatasetIR:
    semantic_id: str; model: str; name: str
    datasource: str
    primary_key: tuple[str, ...]
    description: str | None
    ai_context: AiContextIR
    python_symbol: str
    location: SourceLocation
    # dataset_provenance 不进 IR；materialize-time 决定，存 runtime_metadata

@dataclass(frozen=True)
class FieldIR:
    semantic_id: str; model: str; dataset: str; name: str
    description: str | None
    ai_context: AiContextIR
    is_time_field: bool
    data_type: str | None
    granularity: str | None
    required_prefix: str | None
    python_symbol: str
    location: SourceLocation

@dataclass(frozen=True)
class DecompositionIR:
    kind: Literal["sum", "ratio", "weighted_average"]
    components: dict[str, str]   # name -> metric semantic_id

@dataclass(frozen=True)
class MetricIR:
    semantic_id: str; model: str; name: str
    datasets: tuple[str, ...]
    is_derived: bool
    decomposition: DecompositionIR
    provenance: ProvenanceIR
    description: str | None
    ai_context: AiContextIR
    body_ast_hash: str
    python_symbol: str
    location: SourceLocation

@dataclass(frozen=True)
class RelationshipIR:
    semantic_id: str; model: str; name: str
    from_dataset: str; to_dataset: str
    from_fields: tuple[str, ...]   # field semantic_ids
    to_fields: tuple[str, ...]
    description: str | None
    ai_context: AiContextIR
    location: SourceLocation
```

### 3.4 Refs

decorator 返回 `_BaseRef` 子类而不是原 function：

```python
class _BaseRef:
    semantic_id: str
    kind: SymbolKind
    def __repr__(self) -> str: ...

class DatasourceRef(_BaseRef): ...
class DatasetRef(_BaseRef): ...
class FieldRef(_BaseRef): ...
class TimeFieldRef(_BaseRef): ...
class MetricRef(_BaseRef): ...
class RelationshipRef(_BaseRef): ...
```

这关掉了 `@ms.metric` 后还能像普通函数那样被 user code 直接调用的歧义。

`FieldRef` 和 `TimeFieldRef` 在 base metric body 中可以被调用——这是设计要求"凡是 row-level 中间值都先抽成 field"的必要语法。具体实现：`FieldRef.__call__(parent_table)` 把 self 解析到 sidecar 中的 user fn 并 invoke。`MetricRef` 不可调用，防止 implicit composition；derived metric 用 `ms.component(...)` sentinel 表达组合。`DatasourceRef` 和 `DatasetRef` 不可调用（DatasetRef 通过 `@ms.dataset(datasource=...)` 当 metadata 用）。

## 4. Authoring 层

### 4.1 装饰器签名（全部 keyword-only）

```python
def model(*, name: str, default: bool = True, description: str | None = None,
          ai_context: AiContext | None = None) -> None: ...

def datasource(*, name: str | None = None, backend_type: str,
               model: str | None = None, description: str | None = None,
               ai_context: AiContext | None = None) -> DatasourceRef: ...

def dataset(*, name: str | None = None, datasource: DatasourceRef | str,
            primary_key: list[str] | None = None,
            model: str | None = None, description: str | None = None,
            ai_context: AiContext | None = None
            ) -> Callable[[Callable[[IbisBackend], ibis.Table]], DatasetRef]: ...

def field(*, name: str | None = None, dataset: DatasetRef | str,
          model: str | None = None, description: str | None = None,
          ai_context: AiContext | None = None
          ) -> Callable[..., FieldRef]: ...

def time_field(*, name: str | None = None, dataset: DatasetRef | str,
               data_type: Literal["date", "datetime", "timestamp"],
               granularity: Literal["year", "quarter", "month", "week", "day", "hour"],
               required_prefix: str | None = None,
               model: str | None = None, description: str | None = None,
               ai_context: AiContext | None = None
               ) -> Callable[..., TimeFieldRef]: ...

def metric(*, name: str | None = None,
           datasets: list[DatasetRef | str] | None = None,
           decomposition: DecompositionBuilder,
           source_sql: str | None = None, source_dialect: str | None = None,
           source_document: str | None = None, source_notes: str | None = None,
           provenance: Literal["python_native", "unverified"] | None = None,
           model: str | None = None, description: str | None = None,
           ai_context: AiContext | None = None
           ) -> Callable[..., MetricRef]: ...

def relationship(*, name: str | None = None,
                 from_: DatasetRef | str, to: DatasetRef | str,
                 from_fields: list[FieldRef | str],
                 to_fields: list[FieldRef | str],
                 model: str | None = None, description: str | None = None,
                 ai_context: AiContext | None = None) -> RelationshipRef: ...
```

注意 `relationship` 是顶层 metadata call，**不是 decorator**。

### 4.2 LoaderContext + Default model 解析

```python
_LOADER_CTX: ContextVar[LoaderContext | None] = ContextVar(default=None)
```

decorator 第一行 `_LOADER_CTX.get() is None` → `OutsideLoaderContextError`。

model_name 解析顺序：

1. 用户显式传 `model=` → 使用，触发 organization 校验。
2. 否则查 `LoaderContext.default_model`（来自当前 model 目录 `_model.py` 的 `ms.model(default=True)`）。
3. 否则 `MissingModelError`。

Default model 作用域：仅当前 model 目录顶层 sibling files；不向子目录传播；不随 `from x import *` 跨 module boundary；只在 loader 加载该目录的窗口内生效。

### 4.3 `ms.component` sentinel

```python
def component(name: str, /) -> ComponentExpr: ...
```

- 参数必须字面量字符串；validator AST 阶段强制；运行时也校验非空 str。
- 只能在 derived metric 装饰阶段被调用：通过另一 contextvar `_ACTIVE_DECOMPOSITION: ContextVar[DecompositionIR | None]` 控制。base metric / 模块顶层 → `OutsideDerivedMetricBodyError`。
- 调用时 name 必须在 `decomposition.components.keys()` 中；不在则 `InvalidComponentNameError`。
- 返回 `_ComponentSentinel`；它实现 `ComponentExpr` Protocol；`__add__`/`__sub__`/`__mul__`/`__truediv__`/`__neg__` 及反向 ops 返回 `_BinOpSentinel`。
- Materializer 后续 walk sentinel tree。

## 5. Loader 流水线

### 5.1 Two-pass 算法

**Pass 1 — Discovery + Collect**：

1. enumerate `<root>/<model>/_model.py`（只看顶层子目录）。
2. 对每个 model 目录：
   - 进入 `LoaderContext(current_model_file=_model.py)` 执行 `_model.py`；校验 `ms.model()` 调用且 name 等于目录名；记录 default_model。
   - 然后进入 `LoaderContext(default_model=<this_model>)` 依次执行 sibling .py（排除 `_model.py`、`_exports.py`、以 `.` 开头、以 `_test.py` / `test_*.py` 结尾）。
3. decorator 推 `(IR, callable)` 到 `ctx.pending_objects`。
4. registry 按 kind 收集 dict keyed by semantic_id。

**Pass 2 — Resolve + Validate**：

1. 解析所有 ref 字符串 / forward references。
2. 跑 `validator.assembly_validate(registry)`。
3. freeze registry → `LoadResult(status="ready" | "errored", errors=[...])`。

### 5.2 关键细节

- **`sys.path` 临时注入**：进入 `load()` 顶部 `sys.path.insert(0, str(root.parent))`；finally 块 pop。
- **execute 用 `runpy.run_path(file, init_globals={"__name__": ...})`**：让 `from . import x` 工作。
- **sys.modules 清理**：`reload()` 时 pop 所有以 root 为前缀的模块。
- **errors 累积不短路**：单 model 目录失败不影响其他；assembly_validate 收集所有错误。
- **子目录不递归**：`sales/subdomain/*.py` 不被 loader 拾取；用户若要 helper 自己 `from .util import x`。

### 5.3 `find_project`

```python
def find_project(start_dir: str | Path = ".") -> SemanticProject | None:
    """
    从 start_dir.resolve() 向上找 .marivo/semantic/；
    命中目录即 SemanticProject(root)；
    命中非目录文件 → InvalidProjectError；
    走到 fs root 都没命中 → None。
    空目录算合法 project（load 后 list_models() 返回 []）。
    """
```

## 6. Validator

### 6.1 三层校验

```python
# Layer 1: decorator-time（在 decorator 内同步）
def validate_decorator_call(kind: SymbolKind, payload: dict) -> None: ...

# Layer 2: AST whitelist（在 metric/derived metric 装饰阶段对 user fn AST 扫描）
def validate_metric_body_ast(fn: Callable, mode: Literal["base", "derived"]) -> str:
    """Returns body AST hash for IR."""

# Layer 3: assembly-time（loader Pass 2 调用，跨对象关系）
def assembly_validate(registry: Registry) -> list[StructuredError]:
    """返回 error 列表，不 raise。"""
```

AST 校验在 decorator-time 跑，确保行号精确。

### 6.2 Base metric body AST 白名单

允许：

- 单 `Return` stmt。
- dataset arg 上的 attribute / method call (`.filter`, `.cast`, `.sum`, `.nunique`, `.amount`, ...)。
- 调用已注册的 field/time_field refs (返回 ibis column)。
- 数字 / 字符串 / None 字面量。
- 二元 / 一元算术、布尔 op、comparison、conditional expression。

禁止：

- 多 statement、赋值、import、for/while/with/try、async/await。
- 调用 decorated metric refs（避免 implicit composition）。
- `.sql` / `.raw_sql` attribute 或 method 调用 → `SQL_ESCAPE_HATCH`。

### 6.3 Derived metric body AST 白名单（更严）

允许：

- `ms.component("<literal>")` 字符串字面量调用。
- 数字字面量、`None`。
- 二元 `+` `-` `*` `/` 和一元 `-`。
- 括号。

禁止：

- 任何其他函数调用（包括 `abs`、`ms.literal`）。
- attribute access、subscription、comparison、boolean op、conditional expression、字符串字面量（除 `ms.component(...)` 唯一位置参数外）。
- dataset / field / time_field / 任何非 component 对象引用。

### 6.4 形态判定（metric base vs derived）

| `datasets` | `decomposition` | body 形态 | 判定 |
|---|---|---|---|
| 非空 | 任意 | 用 dataset aliases | base metric |
| 省略 / 空 | `ms.sum()`（无 components） | 任意 | `MISSING_DATASETS` error |
| 省略 / 空 | 带 components 的 ratio / weighted_average | 仅 `ms.component(...)` + 算术 | derived metric |
| 非空 | 任意 | 用 `ms.component(...)` | error (混合形态) |
| 省略 | 任意 | 既无 datasets 又无 components | error |

## 7. Reader / SemanticProject

### 7.1 API

```python
class SemanticProject:
    def __init__(self, root: str | Path) -> None: ...

    # lifecycle
    def load(self) -> LoadResult: ...
    def reload(self) -> LoadResult: ...
    def is_ready(self) -> bool: ...
    def errors(self) -> tuple[SemanticError, ...]: ...

    # listings
    def list_models(self) -> list[ModelSummary]: ...
    def list_datasources(self) -> list[DatasourceSummary]: ...
    def list_datasets(self, *, model: str | None = None) -> list[DatasetSummary]: ...
    def list_metrics(self, *,
        dataset: str | None = None,
        decomposition: Literal["sum", "ratio", "weighted_average"] | None = None,
        provenance_status: ParityStatus | None = None,
    ) -> list[MetricSummary]: ...

    # discovery
    def search(self, query: str, *, kind: SymbolKind | None = None) -> list[SearchHit]: ...

    # dependency graph
    def dependencies(self, name: str) -> DependencyNode: ...
    def dependents(self, name: str) -> DependencyNode: ...

    # describe
    def describe(self, name: str, *,
        compile_sql: bool = False,
        format: Literal["object", "text"] = "object",
        backend_factory: Callable[[str], IbisBackend] | None = None,
    ) -> Description: ...

    # materialize
    def materialize_dataset(self, name: str, *, backend_factory) -> ibis.Table: ...
    def materialize_field(self, name: str, *, backend_factory) -> ibis.Value: ...
    def materialize_metric(self, name: str, *, backend_factory) -> ibis.Value: ...

    # parity
    def parity_check(self, name: str, *,
        backend_factory: Callable[[str], IbisBackend],
        rel_tol: float | None = None, abs_tol: float | None = None,
    ) -> ParityResult: ...
```

读 method 在 `is_ready() == False` 时自动 `load()`；load 进 errored 后所有读 method raise `SemanticLoadFailed`。`reload()` 显式重新加载。

支持类型（与 Description 同为 frozen dataclass）：

```python
@dataclass(frozen=True)
class ModelSummary:
    name: str; description: str | None; default: bool
    object_counts: dict[str, int]   # kind -> count

@dataclass(frozen=True)
class DatasourceSummary:
    semantic_id: str; model: str; name: str; backend_type: str
    description: str | None

@dataclass(frozen=True)
class DatasetSummary:
    semantic_id: str; model: str; name: str
    datasource: str; description: str | None
    dataset_provenance: DatasetProvenance | None   # None 表示尚未 materialize 过

@dataclass(frozen=True)
class MetricSummary:
    semantic_id: str; model: str; name: str
    description: str | None
    decomposition_kind: Literal["sum", "ratio", "weighted_average"]
    is_derived: bool
    parity_status: ParityStatus
    python_symbol: str

@dataclass(frozen=True)
class SearchHit:
    semantic_id: str; kind: SymbolKind
    matched_field: Literal["semantic_id", "name", "description",
                            "business_definition", "synonyms", "examples"]
    matched_snippet: str   # 命中的子串及前后短上下文

@dataclass(frozen=True)
class DependencyNode:
    semantic_id: str; kind: SymbolKind
    children: tuple["DependencyNode", ...]   # 上游 (dependencies) 或下游 (dependents)

@dataclass(frozen=True)
class LoadResult:
    status: Literal["ready", "errored"]
    errors: tuple[SemanticError, ...]
    warnings: tuple[StructuredWarning, ...]

@dataclass(frozen=True)
class StructuredWarning:
    kind: Literal["string_ref", "unverified_provenance", "potentially_fragile_reference"]
    message: str
    refs: tuple[str, ...]
    location: SourceLocation | None

@dataclass(frozen=True)
class ParityResult:
    ok: bool
    expected: float | int | None
    actual: float | int | None
    rel_tol: float | None
    abs_tol: float | None
    error: SemanticParityError | None
```

### 7.2 `search` 确定性

- 大小写不敏感子串匹配。
- 字段优先级：`semantic_id` > `name` > `description` > `ai_context.business_definition` > `ai_context.synonyms` > `ai_context.examples`。
- 次序内按 `semantic_id` 字典序稳定排序。
- 不做 embedding / 语义相似度。

### 7.3 Description dataclass

```python
@dataclass(frozen=True)
class Description:
    semantic_id: str
    kind: SymbolKind
    model: str
    name: str
    python_symbol: str
    description: str | None
    business_definition: str | None
    guardrails: tuple[str, ...]
    synonyms: tuple[str, ...]
    examples: tuple[str, ...]
    parity_status: ParityStatus | None     # metric 才有
    source_sql: str | None
    source_dialect: str | None
    source_document: str | None
    compiled_sql: str | None
    compile_error: dict[str, Any] | None   # {kind, message, refs}
    dependencies: tuple[str, ...]
    dependents: tuple[str, ...]
    source_location: SourceLocation
    dataset_provenance: DatasetProvenance | None
    primary_key: tuple[str, ...] | None
    granularity: str | None
    required_prefix: str | None

    def to_text(self) -> str: ...
```

## 8. Materializer

### 8.1 设计

```python
class Materializer:
    def __init__(self, project: SemanticProject, backend_factory: Callable[[str], IbisBackend]) -> None:
        self._backend_cache: dict[str, IbisBackend] = {}
        self._dataset_cache: dict[str, ibis.Table] = {}
        self._field_cache: dict[str, ibis.Value] = {}
        self._metric_cache: dict[str, ibis.Value] = {}

    def dataset(self, semantic_id: str) -> ibis.Table: ...
    def field(self, semantic_id: str) -> ibis.Value: ...
    def metric(self, semantic_id: str) -> ibis.Value: ...
```

关键设计点：

- **每次 `project.materialize_*` 创建新 Materializer**；backend_factory 不 hold 在 project 上。
- **backend_factory 调用幂等**：以 datasource semantic_id 做 cache key。
- **SQL view 检测**：dataset 物化后 walk ibis expression tree 找 `SQLString` node；命中则该 dataset 的 `dataset_provenance` 改为 `SQL_VIEW`，写到 `project._runtime_metadata: dict[str, DatasetRuntimeMetadata]`（不进 frozen IR）。`DatasetRuntimeMetadata` 形态：

  ```python
  @dataclass(frozen=True)
  class DatasetRuntimeMetadata:
      dataset_provenance: DatasetProvenance
      raw_sql_snippet: str | None     # SQLString node 的文本，supplies describe
      detected_at: datetime
  ```
- **cross-datasource fail-closed**：base metric `datasets` 中所有 dataset 必须同 datasource；derived metric 所有 component 隐含 datasource 集合也必须一致。否则 `CROSS_DATASOURCE_NOT_SUPPORTED`。
- **derived metric materialize**：walk sentinel tree，每 leaf 通过 `metric.decomposition.components[name]` 拿到 component metric semantic_id，递归 materialize 替换 leaf；BinOp 节点套用对应 Ibis 算术。

### 8.2 compile_sql 契约

```python
def _compile_for_describe(
    project, target_datasource: DatasourceIR, expr,
    backend_factory: Callable | None,
) -> tuple[str | None, dict | None]:
    """
    - 有 backend_factory: 取 backend, dialect 不匹配 target_datasource.backend_type → BACKEND_MISMATCH。
    - 无 backend_factory: 用 ibis.get_backend(target_datasource.backend_type) 建 in-memory dry backend。
      若 backend 不可实例化 → 返回 (None, {kind: "compile_error", ...})。
    - 编译失败 → (None, {kind, message, refs}); strict=True 时上层 raise。
    """
```

## 9. Parity 与 status 传播

### 9.1 base metric parity

```python
def parity_check(project, metric_id, *, backend_factory, rel_tol, abs_tol) -> ParityResult:
    """
    - metric 必须存在；is_derived → raise (derived 不直接 SQL parity)
    - source_sql / source_dialect 必填，且 dialect == datasource.backend_type
    - 所有依赖 datasets 必须同 datasource
    - materialize → execute → 单 scalar
    - backend.sql(source_sql) → execute → 单 scalar
    - exact / rel_tol / abs_tol 比较
    """
```

### 9.2 status 计算与 derived 弱传播

每个 metric 的 effective parity status 计算分两步：

**Step 1 — self status**（base 和 derived 都走）：

| `ProvenanceIR.declared_status` | 最近一次 `parity_check` 结果 | self status |
|---|---|---|
| `"python_native"` | 任意 | `PYTHON_NATIVE` |
| `"unverified"` | 任意 | `UNVERIFIED` |
| `None`（即声明了 SQL triple） | 无 / 未跑 | `UNVERIFIED` |
| `None` | `ok=True` | `VERIFIED` |
| `None` | `ok=False` | `DRIFTED` |

**Step 2 — derived metric propagation**（仅 `is_derived=True`）：

```python
def propagated_parity_status(project, metric_id) -> ParityStatus:
    self_status = compute_self_status(project, metric_id)
    if not metric.is_derived:
        return self_status

    component_statuses = [
        propagated_parity_status(project, comp_id)
        for comp_id in metric.decomposition.components.values()
    ]
    all_statuses = [self_status, *component_statuses]

    # 弱传播：取最弱者
    if any(s == DRIFTED for s in all_statuses):     return DRIFTED
    if any(s == UNVERIFIED for s in all_statuses):  return UNVERIFIED
    # 此时所有 status ∈ {VERIFIED, PYTHON_NATIVE}
    if all(s == VERIFIED for s in all_statuses):    return VERIFIED
    return PYTHON_NATIVE
```

### 9.3 状态汇总

| 数据 | 不可变 | 备注 |
|---|---|---|
| `Registry` (IR 集合) | ✅ | load 后 frozen |
| `sidecar` (callable map) | ✅ | load 后 frozen |
| `runtime_metadata` (sql_view 标记) | 写一次 | materialize 首次发现时写入 |
| `_parity_results` (cache) | 可变 | parity_check 更新；reload 清空 |
| `_backend_cache` (Materializer 内) | 可变 | 单次 materialize 调用作用域 |

`parity_status` 是计算属性，不存 IR；每次 `describe` / `list_metrics(provenance_status=...)` 现算。

## 10. Errors

### 10.1 类层级

```python
class SemanticError(Exception):
    kind: str
    semantic_refs: tuple[str, ...]
    location: SourceLocation | None
    hint: str | None
    details: dict[str, Any]

    def __str__(self) -> str: ...
        # 共享 template:
        # [kind] message\n  refs: ...\n  at: file:line\n  hint: ...

class SemanticDecoratorError(SemanticError): ...
class SemanticLoadError(SemanticError): ...
class SemanticRuntimeError(SemanticError): ...
class SemanticParityError(SemanticError): ...
```

### 10.2 ErrorKind enum (完整集)

decorator-time:

- `duplicate_name`, `missing_model`, `missing_datasets`, `invalid_ref`,
  `invalid_decomposition`, `invalid_component_body`, `outside_loader_context`,
  `outside_derived_metric_body`, `metric_body_not_single_return`,
  `invalid_ai_context`, `sql_escape_hatch`

assembly-time:

- `model_file_missing`, `model_file_mismatch`, `missing_dataset_ref`,
  `missing_field_ref`, `missing_metric_ref`, `cross_model_cycle`,
  `hour_time_field_prefix_missing`, `invalid_relationship_endpoint`,
  `organization_error`, `invalid_project`

runtime:

- `metric_not_found`, `materialize_failed`, `backend_mismatch`,
  `compile_error`, `cross_datasource_not_supported`

parity:

- `source_sql_missing`, `unverified_provenance`,
  `parity_value_mismatch`, `parity_not_scalar`

### 10.3 单一 raise helper

```python
def _raise(kind: ErrorKind, message: str, *,
           cls: type[SemanticError] = SemanticDecoratorError,
           refs: Sequence[str] = (),
           location: SourceLocation | None = None,
           hint: str | None = None,
           details: dict | None = None) -> NoReturn: ...
```

Hint 文案集中在 `errors.HINTS: dict[ErrorKind, Callable[..., str]]`。

## 11. CLI

### 11.1 `check` subcommand

```
python -m marivo.semantic_py check [options]

Options:
  --project DIR           显式项目根；缺省走 find_project(cwd)
  --strict-provenance     unverified metric (自身或传播) 非零退出
  --parity                对所有 source_sql metric 跑 parity
  --format text|json      默认 text
```

### 11.2 Exit codes

- `0` ready 且无 strict 违例
- `1` 有结构化错误
- `2` `--strict-provenance` 触发的 unverified
- `3` `--parity` 失败
- `4` `--project` 不存在 / 非目录 / find_project 失败

### 11.3 `--format=json` schema (freeze, v1)

```json
{
  "schema_version": "1",
  "project_root": "/abs/path",
  "status": "ready" | "errored",
  "models": [{"name": "sales", "default": true,
              "object_counts": {"dataset": 3, "metric": 5}}],
  "errors": [{"kind": "...", "class": "SemanticLoadError",
              "message": "...", "refs": [...], "location": {...},
              "hint": "...", "details": {...}}],
  "warnings": [{"kind": "string_ref"|"unverified_provenance"|"potentially_fragile_reference",
                "message": "...", "refs": [...], "location": {...}}],
  "parity": [{"metric": "sales.revenue", "ok": true,
              "expected": ..., "actual": ...}]
}
```

### 11.4 text 格式

每条错误一段：

```
[kind] short_message
  refs: sales.revenue, sales.orders
  at: /abs/path/_model.py:42
  hint: ...
```

### 11.5 实现位置

`cli/__main__.py` 只 argparse + dispatch；逻辑在 `cli/check.py`。`refactor rename` subcommand 留 stub 抛 `NotImplementedError("scheduled for next iteration")`，不出现在 `--help`。

## 12. analysis_py 衔接

### 12.1 三处 import 切换（同 PR）

| 文件 | 改动 |
|---|---|
| `marivo/analysis_py/intents/observe.py:101-102` | `from marivo.semantic_py import reader; reader.list_metrics(...)` → 通过 session 上的 `SemanticProject` 实例调 `project.list_metrics(...)` |
| `marivo/analysis_py/session/attach.py:107` | 改构造 `SemanticProject(root=session.semantic_root)` 然后 `.load()`；load 失败时 raise `analysis_py.errors.SemanticProjectNotReady` 透传 `SemanticError` 列表 |
| `marivo/analysis_py/errors.py:82,230` | hint 字符串保留 `import marivo.semantic_py as ms`；module-level call 改为 `ms.find_project().list_metrics()` 或 `project.list_metrics()` |

### 12.2 Session 持有 SemanticProject

`Session.semantic_project: SemanticProject` 必须存在。构造时由 `session/attach.py` 注入；observe / compare / 其他 intent 通过它访问。

### 12.3 frame summary 显示 parity_status

`analysis_py` frame metadata 从 metric 拿 `Description.parity_status` 或 `MetricSummary.parity_status`，在 `frame.summary()` 输出中可见。这条**在本轮范围**。

### 12.4 不在本轮

- `analysis_py.observe` 完整改用 `project.describe()` 拿 metric metadata（仅替换 source，行为不变）—— follow-up PR。

## 13. Skill examples 同 PR 更新

### 13.1 受影响文件

`marivo-skill/marivo-py-semantic/references/examples/`:

- `01_register_datasource.py` → 顶层 `warehouse = ms.datasource(...)` 形态
- `02_declare_dataset.py` → `@ms.dataset(datasource=warehouse, ...)` keyword-only
- `03_define_metric_aggregate.py` → `@ms.metric(datasets=[orders], decomposition=ms.sum(), ...)`
- `04_define_metric_derived.py` → 改用 `ms.component("numerator") / ms.component("denominator")`
- `99_pitfall_dataset_without_datasource.py` → 拒绝场景；用顶层 call 演示
- `_fixtures/tiny_db.py` → 不变（只是 DuckDB schema）

`marivo-skill/marivo-py-analysis/references/examples/_fixtures/tiny_semantic.py`:

- 改为完整 `.marivo/semantic/<model>/_model.py` 项目结构（提供 fixture 用 tmp_path 复制）。

### 13.2 例子约束

- 单文件 ≤ 60 行（`make examples-check` 阈值）。
- self-contained：每个例子顶部 import + 完整可运行片段。
- 用 `ms.model(name="sales")` 顶部入口；其余 decorators 省略 `model=`（依赖默认 `default=True`）。

### 13.3 `make examples-check` 通过条件

- `__init__.py` 的 `__all__` 与 examples 实际引用一致。
- AST 形态符合 v1.1 contract（顶层 datasource call、metric `datasets=` kwarg、`ms.component(...)` 用法）。
- 现有 examples-check 已经按 AST 做静态检查，更新 examples 即可通过。

## 14. 测试策略

### 14.1 共享 fixture

```python
# tests/conftest.py (新加片段)
@pytest.fixture
def semantic_project_factory(tmp_path):
    def _make(files: dict[str, str], load: bool = True) -> SemanticProject:
        root = tmp_path / ".marivo" / "semantic"
        root.mkdir(parents=True)
        for rel, src in files.items():
            full = root / rel
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(src)
        project = SemanticProject(root=root)
        if load:
            project.load()
        return project
    return _make

@pytest.fixture
def duckdb_backend():
    import ibis
    con = ibis.duckdb.connect(":memory:")
    con.con.execute("CREATE TABLE orders (...)")
    return con
```

### 14.2 测试文件重组

删除现有 11 个 `test_semantic_py_*.py`，新建 9 个聚焦文件：

| 文件 | 覆盖范围 |
|---|---|
| `test_semantic_py_loader.py` | discovery、two-pass、`_model.py` 校验、sys.path、`find_project`、子目录不递归、`_exports.py` 跳过 |
| `test_semantic_py_authoring.py` | decorator 签名、name vs python_symbol、default model 解析、`ms.component` contextvar |
| `test_semantic_py_validator.py` | decorator-time 校验、AST 白名单 (base/derived)、形态判定四元决策、organization、SQL escape hatch |
| `test_semantic_py_assembly.py` | refs 解析、跨 model ref、cycle detect、relationship 端点 + 字段 arity、PK warning |
| `test_semantic_py_reader.py` | `SemanticProject` methods、search 确定性、dependencies / dependents、Description 字段 |
| `test_semantic_py_materializer.py` | dataset/field/metric materialize、derived component 替换、SQL view 检测、cross-datasource fail-closed、backend cache 作用域 |
| `test_semantic_py_compile.py` | compile_sql 编译、dialect mismatch、dry compiler 路径、compile_error 结构 |
| `test_semantic_py_parity.py` | base parity ok/fail、derived 不直接 parity、propagation 4 case |
| `test_semantic_py_cli.py` | exit codes、json schema、warnings 类型、`--strict-provenance` 触发 |

6 个 `test_analysis_py_*.py` 同 PR 更新仅 import 与 fixture 注入路径。

### 14.3 TDD slice 顺序

每 slice 闭环跑 `make test TESTS='tests/test_semantic_py_*.py' && make typecheck && make lint`。

- **Slice 0** — 骨架可空载（`__init__.py` 公开 surface stub、`typing.py`、`errors.py`、`ir.py`，`test_imports.py`）。
- **Slice 1** — 单文件 happy path 能 load（最小 datasource + dataset + base metric）。
- **Slice 2** — default model + name 规则。
- **Slice 3** — two-pass loader + 跨文件 ref。
- **Slice 4** — base metric materialize (真 DuckDB)。
- **Slice 5** — AST 白名单 (base metric)。
- **Slice 6** — derived metric + `ms.component`。
- **Slice 7** — relationship 顶层 call + field refs。
- **Slice 8** — reader 完整 surface (search / deps / Description)。
- **Slice 9** — compile_sql + describe 编译路径。
- **Slice 10** — parity + propagation。
- **Slice 11** — find_project + ai_context schema。
- **Slice 12** — CLI check (`subprocess.run`)。
- **Slice 13** — analysis_py 衔接 + skill examples + `make examples-check`。

最后跑 `make test && make examples-check` 全套绿。

### 14.4 测试反模式（禁止）

- 不允许用 `monkeypatch` 撬开 `LoaderContext`。
- 不允许直接构造 `Registry` 或 `MetricIR` 注入。
- 不允许 mock backend，用真 DuckDB in-memory。
- 不允许 skip "未来实现"——slice 顺序保证测试和实现同步推进。

## 15. 验证 gate（PR 合入前）

```bash
make test                                   # 全套绿，含新 9 个 + 6 个 analysis_py
make typecheck                              # mypy 不能引入新 ignore；importlinter contract 不能 weak
make lint
make examples-check                         # 6 个 example + cross-skill fixture 通过
.venv/bin/python -m marivo.semantic_py check --project <demo> --format=json   # 冒烟
```

importlinter 现有 `analysis_py-independence` 与 `runtime-does-not-depend-on-analysis_py` contract 不可弱化；本次重写后 `semantic_py` 仍只依赖 ibis + 标准库。

## 16. 风险与开放问题

- **`runpy.run_path` 与 `from . import x` 互操作**：需在 Slice 3 早期验证；若 `runpy` 不能给 `.marivo.semantic.<model>.<file>` 真正 module spec，则 fallback 用 `importlib.util.spec_from_file_location` 手工构造 spec 注入 `sys.modules`。
- **Ibis SQL view 检测 node 类型**：不同 ibis 版本可能用不同 op 名（`SQLStringView` / `SQLQuery` / 旧 `SQLString`），Slice 4 实现时锁版本范围；新增 dataset 测试需含 SQL view 实例。
- **DuckDB ibis 接口稳定性**：Slice 1+ 起的真 backend 测试可能因 ibis 升级抖动；测试通过 `tests/conftest.py` 集中适配。
- **derived metric 引用未注册 component 的早期失败时机**：collect 阶段不知 component metric 是否已注册，必须延迟到 Pass 2；但 decorator-time 至少校验 `ms.component("<literal>")` 的 name 在当前 decomposition.components.keys() 中——这一信息已经在 decoration 调用现场提供。
- **`_runtime_metadata` 跨 reload 不持久**：意味着 reload 后首次 describe SQL view 信息丢失，直到再次 materialize。这是设计文档允许的（runtime metadata 显式声明非 IR），但 agent 工作流需明示"reload 后 describe 的 dataset_provenance 可能短暂落到默认值"。
- **`analysis_py.session.attach` 拥抱失败的 SemanticProject**：必须把所有 `SemanticError` 透传给上层，否则 frame summary 无法显示 root cause。需在 Slice 13 验证。

## 17. 与设计文档的契约对齐

本 spec 直接对应 [`docs/specs/semantic/python-semantic-layer.md`](../../specs/semantic/python-semantic-layer.md) v1.1 "Breaking 目标" 全集，除以下故意 defer：

| v1.1 项 | 本 spec | 备注 |
|---|---|---|
| 所有语义对象显式 `model=` 或显式 default model | ✅ Section 4.2 |  |
| `ms.model(...)` 只能出现在 `<root>/<model>/_model.py` | ✅ Section 5.1 + 6.3 |  |
| `_model.py` 可作 single-file quick path | ✅ Section 5.1 |  |
| Metric 显式 `datasets=[...]` | ✅ Section 4.1 |  |
| Base + derived 统一用 `@ms.metric` | ✅ Section 6.4 形态判定 |  |
| `ms.component("<name>")` sentinel | ✅ Section 4.3 + 6.3 |  |
| Derived metric body AST 白名单 | ✅ Section 6.3 |  |
| Derived parity status 弱传播 | ✅ Section 9.2 |  |
| Datasource / relationship 改顶层 call | ✅ Section 4.1 |  |
| Relationship join keys 改 field/time_field refs | ✅ Section 4.1 |  |
| Reader 主 API 迁到 `SemanticProject` methods | ✅ Section 7.1 |  |
| Metric provenance status 始终存在 | ✅ Section 3.3 + 9.2 |  |
| Parity status 在 metric/frame/describe 可见 | ✅ Section 7.3 + 12.3 |  |
| Field/time_field 不要求 provenance | ✅ Section 3.3 |  |
| Dataset SQL view 显式 `dataset_provenance="sql_view"` | ✅ Section 8.1 |  |
| `check` CLI fresh-process | ✅ Section 11 |  |
| `check` 缺省 find_project、`--strict-provenance`、字符串 refs 提示 | ✅ Section 11.1 + 11.3 |  |
| **refactor rename CLI** | ⏸ defer | 留 stub，不暴露 `--help` |
| Loader two-pass | ✅ Section 5.1 |  |
| `find_project()` 向上查找 | ✅ Section 5.3 |  |
| Reader search / dependencies / dependents | ✅ Section 7.1 |  |
| `describe(compile_sql=True)` 返回结构化对象 | ✅ Section 7.3 + 8.2 |  |
| `name=` 是 identity，python_symbol 是 alias | ✅ Section 3.3 |  |
| Error kind → action 映射 | ✅ Section 10 (errors.HINTS) |  |
| `ai_context` 固定 schema | ✅ Section 2.3 + 3.3 |  |

不在本 spec 也不在 v1.1 breaking 目标的项：parity fixture lifecycle、generated SQL diff、relationship-aware materialization、PK sample validation、跨 dialect 编译失败分类——均归 "后续演进"。
