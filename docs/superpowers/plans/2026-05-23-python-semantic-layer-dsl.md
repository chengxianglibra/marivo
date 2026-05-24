# Python Semantic Layer DSL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `marivo.semantic_py`, a Python-native semantic DSL for defining datasources, datasets, fields, metrics, relationships, source SQL provenance, and ibis-backed materialization.

**Architecture:** The first implementation keeps the Python semantic layer self-contained under `marivo/semantic_py/` and treats Python files as the source of truth. A `SemanticProject` owns registry state for one semantic root, while module-level `reader` APIs are dev conveniences over a default project. Decorators capture metadata and callables; validators produce structured errors; materializers bind callables to caller-provided ibis backends; provenance/parity APIs preserve SQL business definitions for agent-verifiable migration.

**Tech Stack:** Python 3.12+, dataclasses, `ast`, `inspect`, `importlib`, `threading.RLock`, `ibis-framework>=12.0.0` (latest stable on PyPI as of 2026-05-23), optional `duckdb` test backend via the existing `duckdb` extra.

---

## File Structure

- Create `marivo/semantic_py/__init__.py` for public exports as `import marivo.semantic_py as ms`.
- Create `marivo/semantic_py/errors.py` for structured error types.
- Create `marivo/semantic_py/ir.py` for registry IR dataclasses and source provenance models.
- Create `marivo/semantic_py/registry.py` for `SemanticProject`, `PySemanticRegistry`, and scoped active registry management.
- Create `marivo/semantic_py/builders.py` for `sum`, `ratio`, `weighted_average`, `ref`, and expression helper factories.
- Create `marivo/semantic_py/decorators.py` for `model`, `datasource`, `dataset`, `field`, `time_field`, `metric`, and `relationship`.
- Create `marivo/semantic_py/validator.py` for decorator-time and assembly-time validation.
- Create `marivo/semantic_py/loader.py` for trusted local project loading and reload-safe module namespacing.
- Create `marivo/semantic_py/reader.py` for module-level default project read/materialization APIs.
- Create `marivo/semantic_py/parity.py` for SQL provenance comparison helpers.
- Create `marivo/semantic_py/testing.py` for isolated registry/project helpers.
- Modify `pyproject.toml` to add `ibis-framework>=12.0.0` and include ibis/duckdb in the dev test surface.
- Create `tests/test_semantic_py_ir.py`, `tests/test_semantic_py_decorators.py`, `tests/test_semantic_py_validation.py`, `tests/test_semantic_py_loader.py`, `tests/test_semantic_py_materialization.py`, and `tests/test_semantic_py_parity.py`.
- Create `docs/specs/semantic/python-semantic-layer.md` for the public DSL guide.

## Task 1: Dependency And Package Skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `marivo/semantic_py/__init__.py`
- Create: `marivo/semantic_py/errors.py`
- Create: `marivo/semantic_py/ir.py`
- Test: `tests/test_semantic_py_ir.py`

- [ ] **Step 1: Write the failing IR and export test**

Create `tests/test_semantic_py_ir.py`:

```python
from __future__ import annotations

from marivo.semantic_py.ir import (
    DatasourceIR,
    DecompositionIR,
    MetricIR,
    MetricReferences,
    SourceLocation,
    SourceProvenance,
)


def test_package_imports() -> None:
    import marivo.semantic_py as ms

    assert ms.__all__ == []


def test_ir_preserves_source_sql_provenance() -> None:
    location = SourceLocation(file="/tmp/semantic/sales/metrics.py", line=12)
    metric = MetricIR(
        name="revenue",
        model_name="sales",
        fn=lambda orders: orders.amount.sum(),
        decomposition=DecompositionIR(kind="sum"),
        description="Total paid revenue",
        ai_context={"synonyms": ["gmv"]},
        references=MetricReferences(datasets=["orders"], metrics=[], fields=[]),
        source_location=location,
        source=SourceProvenance(
            sql="sum(case when pay_status = 1 then pay_amount else 0 end)",
            dialect="trino",
            document="kb://revenue",
            notes="Official finance metric definition.",
        ),
    )

    assert metric.source is not None
    assert metric.source.dialect == "trino"
    assert "pay_status" in metric.source.sql


def test_datasource_ir_is_pure_identity() -> None:
    datasource = DatasourceIR(
        name="warehouse_main",
        backend_type="trino",
        description="Primary warehouse",
        ai_context=None,
        source_location=SourceLocation(file="/tmp/datasources.py", line=3),
    )

    assert datasource.name == "warehouse_main"
    assert datasource.backend_type == "trino"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
make test TESTS=tests/test_semantic_py_ir.py
```

Expected: FAIL with `ModuleNotFoundError: No module named 'marivo.semantic_py'`.

- [ ] **Step 3: Add ibis dependency**

Modify `pyproject.toml`:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "httpx>=0.28.0",
    "ibis-framework>=12.0.0",
    "jinja2>=3.1.0",
    "mcp>=1.0.0",
    "pydantic>=2.9.0",
    "pyyaml>=6.0",
    "uvicorn>=0.32.0",
]

[project.optional-dependencies]
duckdb = ["duckdb>=1.2.2"]
mysql = ["PyMySQL>=1.1"]
trino = ["trino>=0.330"]
all = ["duckdb>=1.2.2", "PyMySQL>=1.1", "trino>=0.330"]
dev = [
    "duckdb>=1.2.2",
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-xdist>=3",
    "pytest-cov>=5.0",
    "datamodel-code-generator>=0.26",
    "ruff>=0.3.0",
    "mypy>=1.9",
    "types-PyYAML>=6.0",
    "import-linter>=2.0",
]
```

- [ ] **Step 4: Create structured errors**

Create `marivo/semantic_py/errors.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class SourceLocation:
    file: str
    line: int


@dataclass(frozen=True)
class SemanticError(Exception):
    phase: Literal["decorator", "assembly", "load", "runtime", "parity"]
    kind: str
    location: SourceLocation | None
    function: str | None
    message: str
    hint: str | None = None
    refs: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        prefix = f"{self.phase}:{self.kind}"
        if self.location is not None:
            prefix = f"{prefix} at {self.location.file}:{self.location.line}"
        return f"{prefix}: {self.message}"


class SemanticDecoratorError(SemanticError):
    pass


class SemanticAssemblyError(SemanticError):
    pass


class SemanticRuntimeError(SemanticError):
    pass


class SemanticParityError(SemanticError):
    pass


class PySemanticNotFound(KeyError):
    def __init__(self, entity: str, name: str) -> None:
        super().__init__(f"{entity} '{name}' not found")
        self.entity = entity
        self.name = name


class SemanticLoadError(Exception):
    def __init__(self, errors: list[SemanticError]) -> None:
        self.errors = errors
        joined = "; ".join(str(error) for error in errors)
        super().__init__(joined)
```

- [ ] **Step 5: Create IR dataclasses**

Create `marivo/semantic_py/ir.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from marivo.semantic_py.errors import SourceLocation


@dataclass(frozen=True)
class SourceProvenance:
    sql: str | None = None
    dialect: str | None = None
    document: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class SymbolRef:
    kind: Literal["metric", "field", "time_field", "datasource"]
    name: str


@dataclass(frozen=True)
class TimeFieldMeta:
    data_type: Literal["date", "timestamp", "string", "integer"]
    granularity: Literal["hour", "day", "week", "month", "quarter", "year"]
    format: str | None = None
    required_prefix: str | None = None


@dataclass(frozen=True)
class DecompositionIR:
    kind: Literal["sum", "ratio", "weighted_average"]
    numerator: str | None = None
    denominator: str | None = None
    weight: str | None = None


@dataclass(frozen=True)
class MetricReferences:
    datasets: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)


@dataclass
class DatasourceIR:
    name: str
    backend_type: str | None
    description: str | None
    ai_context: dict[str, Any] | None
    source_location: SourceLocation


@dataclass
class FieldIR:
    name: str
    dataset_name: str
    fn: Callable[..., Any]
    is_time: bool
    time_meta: TimeFieldMeta | None
    label: str | None
    description: str | None
    ai_context: dict[str, Any] | None
    source_location: SourceLocation
    source: SourceProvenance | None = None


@dataclass
class DatasetIR:
    name: str
    fn: Callable[..., Any]
    datasource_name: str
    primary_key: list[str]
    unique_keys: list[list[str]]
    fields: dict[str, FieldIR]
    description: str | None
    ai_context: dict[str, Any] | None
    source_location: SourceLocation
    source: SourceProvenance | None = None


@dataclass
class MetricIR:
    name: str
    model_name: str
    fn: Callable[..., Any]
    decomposition: DecompositionIR
    description: str | None
    ai_context: dict[str, Any] | None
    references: MetricReferences
    source_location: SourceLocation
    source: SourceProvenance | None = None


@dataclass
class RelationshipIR:
    name: str
    from_dataset: str
    to_dataset: str
    from_columns: list[str]
    to_columns: list[str]
    source_location: SourceLocation
    description: str | None = None


@dataclass
class ModelIR:
    name: str
    description: str | None
    ai_context: dict[str, Any] | None
    datasources: dict[str, DatasourceIR] = field(default_factory=dict)
    datasets: dict[str, DatasetIR] = field(default_factory=dict)
    relationships: dict[str, RelationshipIR] = field(default_factory=dict)
    metrics: dict[str, MetricIR] = field(default_factory=dict)
    source_files: list[str] = field(default_factory=list)
```

- [ ] **Step 6: Create public exports**

Create `marivo/semantic_py/__init__.py`:

```python
__all__: list[str] = []
```

- [ ] **Step 7: Run test and commit**

Run:

```bash
make test TESTS=tests/test_semantic_py_ir.py
```

Expected: PASS.

Commit after the package imports and IR test pass:

```bash
git add pyproject.toml marivo/semantic_py tests/test_semantic_py_ir.py
git commit -m "feat: add python semantic layer IR"
```

## Task 2: Registry, Project Lifecycle, And Symbol References

**Files:**
- Create: `marivo/semantic_py/registry.py`
- Create: `marivo/semantic_py/builders.py`
- Test: `tests/test_semantic_py_decorators.py`

- [ ] **Step 1: Write failing tests for project-scoped registry and refs**

Create `tests/test_semantic_py_decorators.py`:

```python
from __future__ import annotations

import marivo.semantic_py as ms
from marivo.semantic_py.registry import SemanticProject, use_registry


def test_symbol_ref_is_available_for_forward_references() -> None:
    ref = ms.ref("metric.total_users")

    assert ref.kind == "metric"
    assert ref.name == "total_users"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
make test TESTS=tests/test_semantic_py_decorators.py
```

Expected: FAIL with missing `SemanticProject` or missing decorator stubs.

- [ ] **Step 3: Implement builders**

Create `marivo/semantic_py/builders.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from marivo.semantic_py.ir import SymbolRef


@dataclass(frozen=True)
class DecompositionSpec:
    kind: Literal["sum", "ratio", "weighted_average"]
    numerator: SymbolRef | object | None = None
    denominator: SymbolRef | object | None = None
    weight: SymbolRef | object | None = None


def ref(value: str) -> SymbolRef:
    kind, sep, name = value.partition(".")
    if sep != "." or kind not in {"metric", "field", "time_field", "datasource"} or not name:
        raise ValueError("ref must look like 'metric.total_users'")
    return SymbolRef(kind=kind, name=name)  # type: ignore[arg-type]


def sum() -> DecompositionSpec:
    return DecompositionSpec(kind="sum")


def ratio(*, numerator: SymbolRef | object, denominator: SymbolRef | object) -> DecompositionSpec:
    return DecompositionSpec(kind="ratio", numerator=numerator, denominator=denominator)


def weighted_average(*, numerator: SymbolRef | object, weight: SymbolRef | object) -> DecompositionSpec:
    return DecompositionSpec(kind="weighted_average", numerator=numerator, weight=weight)
```

- [ ] **Step 4: Implement registry**

Create `marivo/semantic_py/registry.py`:

```python
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import RLock
from typing import Literal

from marivo.semantic_py.errors import SemanticError
from marivo.semantic_py.ir import ModelIR


@dataclass
class PySemanticRegistry:
    models: dict[str, ModelIR] = field(default_factory=dict)
    state: Literal["unloaded", "loading", "ready", "errored"] = "unloaded"
    load_errors: list[SemanticError] = field(default_factory=list)

    def clear(self) -> None:
        self.models.clear()
        self.state = "unloaded"
        self.load_errors.clear()


@dataclass
class SemanticProject:
    root: str
    registry: PySemanticRegistry = field(default_factory=PySemanticRegistry)
    lock: RLock = field(default_factory=RLock)


_DEFAULT_PROJECT = SemanticProject(root=".")
_REGISTRY_STACK: list[PySemanticRegistry] = [_DEFAULT_PROJECT.registry]


def active_registry() -> PySemanticRegistry:
    return _REGISTRY_STACK[-1]


@contextmanager
def use_registry(registry: PySemanticRegistry) -> Iterator[PySemanticRegistry]:
    _REGISTRY_STACK.append(registry)
    try:
        yield registry
    finally:
        _REGISTRY_STACK.pop()


def default_project() -> SemanticProject:
    return _DEFAULT_PROJECT
```

- [ ] **Step 5: Export builder functions**

Modify `marivo/semantic_py/__init__.py`:

```python
from marivo.semantic_py.builders import ref, ratio, sum, weighted_average

__all__ = [
    "ref",
    "ratio",
    "sum",
    "weighted_average",
]
```

- [ ] **Step 6: Run partial tests**

Run:

```bash
make test TESTS=tests/test_semantic_py_decorators.py::test_symbol_ref_is_available_for_forward_references
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add marivo/semantic_py/__init__.py marivo/semantic_py/builders.py marivo/semantic_py/registry.py tests/test_semantic_py_decorators.py
git commit -m "feat: add python semantic registry"
```

## Task 3: Decorators And Registration

**Files:**
- Create: `marivo/semantic_py/decorators.py`
- Modify: `marivo/semantic_py/builders.py`
- Test: `tests/test_semantic_py_decorators.py`

- [ ] **Step 1: Extend decorator tests**

Append to `tests/test_semantic_py_decorators.py`:

```python
def test_decorators_register_complete_model() -> None:
    project = SemanticProject(root="/tmp/sales")

    with use_registry(project.registry):
        ms.model(name="sales", description="Sales model")

        @ms.datasource(name="warehouse_main", backend_type="duckdb")
        def warehouse_main():
            ...

        @ms.dataset(name="orders", datasource=warehouse_main, primary_key=["order_id"])
        def orders(backend):
            return backend.table("orders")

        @ms.time_field(dataset="orders", data_type="date", granularity="day")
        def order_date(orders):
            return orders.created_at.cast("date")

        @ms.field(dataset="orders", label="dimension")
        def region(orders):
            return orders.region.upper()

        @ms.metric(
            decomposition=ms.sum(),
            source_sql="sum(amount)",
            source_dialect="trino",
            source_document="kb://sales/revenue",
        )
        def revenue(orders):
            return orders.amount.sum()

        @ms.relationship(
            name="orders_to_users",
            from_="orders",
            to="users",
            from_columns=["user_id"],
            to_columns=["user_id"],
        )
        def orders_to_users():
            ...

    model = project.registry.models["sales"]
    assert model.datasources["warehouse_main"].backend_type == "duckdb"
    assert model.datasets["orders"].datasource_name == "warehouse_main"
    assert model.datasets["orders"].fields["order_date"].is_time is True
    assert model.datasets["orders"].fields["region"].label == "dimension"
    assert model.metrics["revenue"].source is not None
    assert model.metrics["revenue"].source.sql == "sum(amount)"
    assert model.relationships["orders_to_users"].from_dataset == "orders"


def test_public_surface_exports_core_builders() -> None:
    assert callable(ms.model)
    assert callable(ms.datasource)
    assert callable(ms.dataset)
    assert callable(ms.metric)
    assert callable(ms.sum)
    assert callable(ms.ratio)
    assert callable(ms.weighted_average)
    assert callable(ms.ref)


def test_project_registry_does_not_share_models_between_projects() -> None:
    first = SemanticProject(root="/tmp/first")
    second = SemanticProject(root="/tmp/second")

    with use_registry(first.registry):
        ms.model(name="sales")

    with use_registry(second.registry):
        ms.model(name="marketing")

    assert sorted(first.registry.models) == ["sales"]
    assert sorted(second.registry.models) == ["marketing"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
make test TESTS=tests/test_semantic_py_decorators.py
```

Expected: FAIL with missing `marivo.semantic_py.decorators`.

- [ ] **Step 3: Implement decorators**

Create `marivo/semantic_py/decorators.py`:

```python
from __future__ import annotations

import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any

from marivo.semantic_py.builders import DecompositionSpec
from marivo.semantic_py.errors import SourceLocation
from marivo.semantic_py.ir import (
    DatasetIR,
    DatasourceIR,
    DecompositionIR,
    FieldIR,
    MetricIR,
    MetricReferences,
    ModelIR,
    RelationshipIR,
    SourceProvenance,
    SymbolRef,
    TimeFieldMeta,
)
from marivo.semantic_py.registry import active_registry


def _location(fn: Callable[..., Any]) -> SourceLocation:
    try:
        line = inspect.getsourcelines(fn)[1]
    except OSError:
        line = 1
    return SourceLocation(file=inspect.getsourcefile(fn) or "<unknown>", line=line)


def _current_model() -> ModelIR:
    registry = active_registry()
    if len(registry.models) == 1:
        return next(iter(registry.models.values()))
    if not registry.models:
        model(name="default")
        return registry.models["default"]
    raise ValueError("multiple models are registered; load-time directory scoping must select one")


def _name_from_ref(value: object, attr: str, expected_kind: str) -> str:
    if isinstance(value, SymbolRef):
        if value.kind != expected_kind:
            raise ValueError(f"expected {expected_kind} ref, got {value.kind}")
        return value.name
    payload = getattr(value, attr, None)
    if isinstance(payload, dict) and isinstance(payload.get("name"), str):
        return payload["name"]
    raise ValueError(f"expected decorated {expected_kind} function or ms.ref('{expected_kind}.name')")


def model(*, name: str, description: str | None = None, ai_context: dict[str, Any] | None = None) -> None:
    registry = active_registry()
    if name in registry.models:
        raise ValueError(f"semantic model '{name}' is already registered")
    registry.models[name] = ModelIR(name=name, description=description, ai_context=ai_context)


def datasource(
    *,
    name: str | None = None,
    backend_type: str | None = None,
    description: str | None = None,
    ai_context: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        ds_name = name or fn.__name__
        entry = DatasourceIR(
            name=ds_name,
            backend_type=backend_type,
            description=description,
            ai_context=ai_context,
            source_location=_location(fn),
        )
        _current_model().datasources[ds_name] = entry

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper.__marivo_datasource__ = {"name": ds_name}  # type: ignore[attr-defined]
        return wrapper

    return decorate


def dataset(
    *,
    datasource: object,
    name: str | None = None,
    primary_key: list[str] | None = None,
    unique_keys: list[list[str]] | None = None,
    description: str | None = None,
    ai_context: dict[str, Any] | None = None,
    source_sql: str | None = None,
    source_dialect: str | None = None,
    source_document: str | None = None,
    source_notes: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        dataset_name = name or fn.__name__
        datasource_name = _name_from_ref(datasource, "__marivo_datasource__", "datasource")
        entry = DatasetIR(
            name=dataset_name,
            fn=fn,
            datasource_name=datasource_name,
            primary_key=primary_key or [],
            unique_keys=unique_keys or [],
            fields={},
            description=description,
            ai_context=ai_context,
            source_location=_location(fn),
            source=SourceProvenance(source_sql, source_dialect, source_document, source_notes),
        )
        _current_model().datasets[dataset_name] = entry
        fn.__marivo_dataset__ = {"name": dataset_name}  # type: ignore[attr-defined]
        return fn

    return decorate


def field(
    *,
    dataset: str,
    name: str | None = None,
    label: str | None = None,
    description: str | None = None,
    ai_context: dict[str, Any] | None = None,
    source_sql: str | None = None,
    source_dialect: str | None = None,
    source_document: str | None = None,
    source_notes: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        field_name = name or fn.__name__
        entry = FieldIR(
            name=field_name,
            dataset_name=dataset,
            fn=fn,
            is_time=False,
            time_meta=None,
            label=label,
            description=description,
            ai_context=ai_context,
            source_location=_location(fn),
            source=SourceProvenance(source_sql, source_dialect, source_document, source_notes),
        )
        model_ir = _current_model()
        model_ir.datasets.setdefault(
            dataset,
            DatasetIR(dataset, lambda backend: backend.table(dataset), "", [], [], {}, None, None, _location(fn)),
        ).fields[field_name] = entry
        fn.__marivo_field__ = {"name": field_name, "dataset": dataset}  # type: ignore[attr-defined]
        return fn

    return decorate


def time_field(
    *,
    dataset: str,
    data_type: str,
    granularity: str,
    name: str | None = None,
    format: str | None = None,
    required_prefix: object | None = None,
    description: str | None = None,
    ai_context: dict[str, Any] | None = None,
    source_sql: str | None = None,
    source_dialect: str | None = None,
    source_document: str | None = None,
    source_notes: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        field_name = name or fn.__name__
        prefix_name = (
            _name_from_ref(required_prefix, "__marivo_time_field__", "time_field")
            if required_prefix is not None
            else None
        )
        entry = FieldIR(
            name=field_name,
            dataset_name=dataset,
            fn=fn,
            is_time=True,
            time_meta=TimeFieldMeta(
                data_type=data_type,  # type: ignore[arg-type]
                granularity=granularity,  # type: ignore[arg-type]
                format=format,
                required_prefix=prefix_name,
            ),
            label="time",
            description=description,
            ai_context=ai_context,
            source_location=_location(fn),
            source=SourceProvenance(source_sql, source_dialect, source_document, source_notes),
        )
        model_ir = _current_model()
        model_ir.datasets.setdefault(
            dataset,
            DatasetIR(dataset, lambda backend: backend.table(dataset), "", [], [], {}, None, None, _location(fn)),
        ).fields[field_name] = entry
        fn.__marivo_time_field__ = {"name": field_name, "dataset": dataset}  # type: ignore[attr-defined]
        return fn

    return decorate


def _metric_ref_name(value: object | None) -> str | None:
    if value is None:
        return None
    return _name_from_ref(value, "__marivo_metric__", "metric")


def metric(
    *,
    decomposition: DecompositionSpec,
    name: str | None = None,
    description: str | None = None,
    ai_context: dict[str, Any] | None = None,
    source_sql: str | None = None,
    source_dialect: str | None = None,
    source_document: str | None = None,
    source_notes: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        metric_name = name or fn.__name__
        entry = MetricIR(
            name=metric_name,
            model_name=_current_model().name,
            fn=fn,
            decomposition=DecompositionIR(
                kind=decomposition.kind,
                numerator=_metric_ref_name(decomposition.numerator),
                denominator=_metric_ref_name(decomposition.denominator),
                weight=_metric_ref_name(decomposition.weight),
            ),
            description=description,
            ai_context=ai_context,
            references=MetricReferences(datasets=list(inspect.signature(fn).parameters)),
            source_location=_location(fn),
            source=SourceProvenance(source_sql, source_dialect, source_document, source_notes),
        )
        _current_model().metrics[metric_name] = entry
        fn.__marivo_metric__ = {"name": metric_name}  # type: ignore[attr-defined]
        return fn

    return decorate


def relationship(
    *,
    name: str,
    from_: str,
    to: str,
    from_columns: list[str],
    to_columns: list[str],
    description: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        _current_model().relationships[name] = RelationshipIR(
            name=name,
            from_dataset=from_,
            to_dataset=to,
            from_columns=from_columns,
            to_columns=to_columns,
            source_location=_location(fn),
            description=description,
        )
        return fn

    return decorate
```

- [ ] **Step 4: Update public exports**

Modify `marivo/semantic_py/__init__.py`:

```python
from marivo.semantic_py.builders import ref, ratio, sum, weighted_average
from marivo.semantic_py.decorators import (
    dataset,
    datasource,
    field,
    metric,
    model,
    relationship,
    time_field,
)

__all__ = [
    "dataset",
    "datasource",
    "field",
    "metric",
    "model",
    "ref",
    "relationship",
    "ratio",
    "sum",
    "time_field",
    "weighted_average",
]
```

- [ ] **Step 5: Run decorator tests**

Run:

```bash
make test TESTS=tests/test_semantic_py_decorators.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add marivo/semantic_py/decorators.py marivo/semantic_py/__init__.py tests/test_semantic_py_decorators.py
git commit -m "feat: register python semantic declarations"
```

## Task 4: AST And Assembly Validation

**Files:**
- Create: `marivo/semantic_py/validator.py`
- Modify: `marivo/semantic_py/decorators.py`
- Test: `tests/test_semantic_py_validation.py`

- [ ] **Step 1: Write validation tests**

Create `tests/test_semantic_py_validation.py`:

```python
from __future__ import annotations

import pytest

import marivo.semantic_py as ms
from marivo.semantic_py.errors import SemanticLoadError
from marivo.semantic_py.registry import SemanticProject, use_registry
from marivo.semantic_py.validator import validate_all


def test_validation_aggregates_missing_references() -> None:
    project = SemanticProject(root="/tmp/invalid")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.metric(decomposition=ms.ratio(numerator=ms.ref("metric.a"), denominator=ms.ref("metric.b")))
        def conversion_rate(orders):
            return orders.converted.sum() / orders.users.sum()

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_all(project.registry)

    kinds = {error.kind for error in exc_info.value.errors}
    assert "MetricDatasetMissing" in kinds
    assert "MetricReferenceMissing" in kinds


def test_time_hour_requires_prefix() -> None:
    project = SemanticProject(root="/tmp/invalid-time")

    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse():
            ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend):
            return backend.table("orders")

        @ms.time_field(dataset="orders", data_type="integer", granularity="hour", format="hh")
        def order_hour(orders):
            return orders.log_hour

    with pytest.raises(SemanticLoadError) as exc_info:
        validate_all(project.registry)

    assert exc_info.value.errors[0].kind == "TimeFieldPrefixMissing"


def test_metric_body_rejects_assignment_at_decorator_time() -> None:
    project = SemanticProject(root="/tmp/invalid-ast")

    with use_registry(project.registry), pytest.raises(Exception) as exc_info:
        ms.model(name="sales")

        @ms.metric(decomposition=ms.sum())
        def revenue(orders):
            total = orders.amount.sum()
            return total

    assert "AstNodeForbidden" in str(exc_info.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
make test TESTS=tests/test_semantic_py_validation.py
```

Expected: FAIL with missing `marivo.semantic_py.validator`.

- [ ] **Step 3: Implement validator**

Create `marivo/semantic_py/validator.py`:

```python
from __future__ import annotations

import ast
import inspect
import textwrap
from collections.abc import Iterable
from typing import Any

from marivo.semantic_py.errors import (
    SemanticAssemblyError,
    SemanticDecoratorError,
    SemanticError,
    SemanticLoadError,
    SourceLocation,
)
from marivo.semantic_py.ir import MetricIR
from marivo.semantic_py.registry import PySemanticRegistry


_FORBIDDEN_NODES = (
    ast.Import,
    ast.ImportFrom,
    ast.FunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.Assign,
    ast.AugAssign,
    ast.AnnAssign,
    ast.If,
    ast.For,
    ast.While,
    ast.Try,
    ast.With,
    ast.Raise,
    ast.Global,
    ast.Nonlocal,
    ast.Yield,
    ast.Await,
)


def validate_function_body(fn: Any, *, decorator: str) -> None:
    try:
        source = textwrap.dedent(inspect.getsource(fn))
        start_line = inspect.getsourcelines(fn)[1]
        file_name = inspect.getsourcefile(fn) or "<unknown>"
    except OSError as exc:
        raise SemanticDecoratorError(
            phase="decorator",
            kind="SourceUnavailable",
            location=None,
            function=getattr(fn, "__name__", None),
            message=f"cannot inspect {decorator} function source",
        ) from exc

    tree = ast.parse(source)
    function_def = next(node for node in tree.body if isinstance(node, ast.FunctionDef))
    nodes = [child for stmt in function_def.body for child in ast.walk(stmt)]
    for node in nodes:
        if isinstance(node, _FORBIDDEN_NODES):
            raise SemanticDecoratorError(
                phase="decorator",
                kind="AstNodeForbidden",
                location=SourceLocation(file=file_name, line=start_line + node.lineno - 1),
                function=getattr(fn, "__name__", None),
                message=f"{decorator} function body cannot contain {type(node).__name__}",
                hint="Use a single return expression built from ibis operations.",
            )


def _error(kind: str, message: str, *, refs: Iterable[str] = ()) -> SemanticAssemblyError:
    return SemanticAssemblyError(
        phase="assembly",
        kind=kind,
        location=None,
        function=None,
        message=message,
        hint=None,
        refs=list(refs),
    )


def _metric_dependencies(metric: MetricIR) -> list[str]:
    refs = []
    for candidate in (
        metric.decomposition.numerator,
        metric.decomposition.denominator,
        metric.decomposition.weight,
    ):
        if candidate:
            refs.append(candidate)
    refs.extend(metric.references.metrics)
    return refs


def _detect_metric_cycle(metrics: dict[str, MetricIR]) -> list[SemanticError]:
    errors: list[SemanticError] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str, path: list[str]) -> None:
        if name in visiting:
            cycle = path[path.index(name) :] + [name]
            errors.append(_error("MetricReferenceCycle", "metric reference cycle detected", refs=cycle))
            return
        if name in visited or name not in metrics:
            return
        visiting.add(name)
        for dep in _metric_dependencies(metrics[name]):
            visit(dep, [*path, dep])
        visiting.remove(name)
        visited.add(name)

    for metric_name in metrics:
        visit(metric_name, [metric_name])
    return errors


def validate_all(registry: PySemanticRegistry) -> None:
    errors: list[SemanticError] = []

    for model in registry.models.values():
        for dataset in model.datasets.values():
            if not dataset.datasource_name:
                errors.append(
                    _error(
                        "DatasetDatasourceMissing",
                        f"dataset '{dataset.name}' has no datasource",
                        refs=[dataset.name],
                    )
                )
            elif dataset.datasource_name not in model.datasources:
                errors.append(
                    _error(
                        "DatasetDatasourceMissing",
                        f"datasource '{dataset.datasource_name}' is not registered",
                        refs=[dataset.datasource_name],
                    )
                )

            for field in dataset.fields.values():
                if field.dataset_name not in model.datasets:
                    errors.append(
                        _error(
                            "FieldDatasetMissing",
                            f"field '{field.name}' references missing dataset '{field.dataset_name}'",
                            refs=[field.dataset_name],
                        )
                    )
                if field.is_time and field.time_meta is not None:
                    if field.time_meta.format in {"hh", "h"} and not field.time_meta.required_prefix:
                        errors.append(
                            _error(
                                "TimeFieldPrefixMissing",
                                f"time field '{field.name}' with hour-only format requires required_prefix",
                                refs=[field.name],
                            )
                        )

        for metric in model.metrics.values():
            for dataset_name in metric.references.datasets:
                if dataset_name not in model.datasets:
                    errors.append(
                        _error(
                            "MetricDatasetMissing",
                            f"metric '{metric.name}' references missing dataset '{dataset_name}'",
                            refs=[metric.name, dataset_name],
                        )
                    )
            for metric_name in _metric_dependencies(metric):
                if metric_name not in model.metrics:
                    errors.append(
                        _error(
                            "MetricReferenceMissing",
                            f"metric '{metric.name}' references missing metric '{metric_name}'",
                            refs=[metric.name, metric_name],
                        )
                    )

        for relationship in model.relationships.values():
            if relationship.from_dataset not in model.datasets:
                errors.append(
                    _error(
                        "RelationshipEndpointMissing",
                        f"relationship '{relationship.name}' references missing dataset '{relationship.from_dataset}'",
                        refs=[relationship.from_dataset],
                    )
                )
            if relationship.to_dataset not in model.datasets:
                errors.append(
                    _error(
                        "RelationshipEndpointMissing",
                        f"relationship '{relationship.name}' references missing dataset '{relationship.to_dataset}'",
                        refs=[relationship.to_dataset],
                    )
                )

        errors.extend(_detect_metric_cycle(model.metrics))

    if errors:
        registry.state = "errored"
        registry.load_errors = errors
        raise SemanticLoadError(errors)

    registry.state = "ready"
    registry.load_errors = []
```

- [ ] **Step 4: Wire decorator-time AST validation**

Modify `marivo/semantic_py/decorators.py` by importing `validate_function_body`:

```python
from marivo.semantic_py.validator import validate_function_body
```

Then add these calls at the start of each expression-bearing `decorate()` function, before building the IR entry:

```python
validate_function_body(fn, decorator="dataset")
```

inside `dataset(...)`, and:

```python
validate_function_body(fn, decorator="field")
```

inside `field(...)`, and:

```python
validate_function_body(fn, decorator="time_field")
```

inside `time_field(...)`, and:

```python
validate_function_body(fn, decorator="metric")
```

inside `metric(...)`.

- [ ] **Step 5: Run validation tests**

Run:

```bash
make test TESTS=tests/test_semantic_py_validation.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add marivo/semantic_py/validator.py tests/test_semantic_py_validation.py
git commit -m "feat: validate python semantic registry"
```

## Task 5: Trusted Loader And Reload Semantics

**Files:**
- Create: `marivo/semantic_py/loader.py`
- Modify: `marivo/semantic_py/registry.py`
- Test: `tests/test_semantic_py_loader.py`

- [ ] **Step 1: Write loader tests**

Create `tests/test_semantic_py_loader.py`:

```python
from __future__ import annotations

from pathlib import Path

from marivo.semantic_py.loader import load_project
from marivo.semantic_py.registry import SemanticProject


def _write_sales_model(root: Path, metric_body: str = "return orders.amount.sum()") -> None:
    model_dir = root / "sales"
    model_dir.mkdir(parents=True)
    (model_dir / "__init__.py").write_text("", encoding="utf-8")
    (model_dir / "_model.py").write_text(
        "import marivo.semantic_py as ms\nms.model(name='sales')\n",
        encoding="utf-8",
    )
    (model_dir / "datasources.py").write_text(
        "import marivo.semantic_py as ms\n"
        "@ms.datasource(name='warehouse')\n"
        "def warehouse():\n"
        "    ...\n",
        encoding="utf-8",
    )
    (model_dir / "datasets.py").write_text(
        "import marivo.semantic_py as ms\n"
        "from .datasources import warehouse\n"
        "@ms.dataset(name='orders', datasource=warehouse)\n"
        "def orders(backend):\n"
        "    return backend.table('orders')\n",
        encoding="utf-8",
    )
    (model_dir / "metrics.py").write_text(
        "import marivo.semantic_py as ms\n"
        "@ms.metric(decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        f"    {metric_body}\n",
        encoding="utf-8",
    )


def test_load_project_imports_semantic_directory(tmp_path: Path) -> None:
    _write_sales_model(tmp_path)
    project = SemanticProject(root=str(tmp_path))

    load_project(project)

    assert project.registry.state == "ready"
    assert sorted(project.registry.models) == ["sales"]
    assert sorted(project.registry.models["sales"].metrics) == ["revenue"]


def test_reload_reexecutes_changed_modules(tmp_path: Path) -> None:
    _write_sales_model(tmp_path)
    project = SemanticProject(root=str(tmp_path))

    load_project(project)
    first_line = project.registry.models["sales"].metrics["revenue"].source_location.line

    metrics = tmp_path / "sales" / "metrics.py"
    metrics.write_text(
        "import marivo.semantic_py as ms\n\n\n"
        "@ms.metric(decomposition=ms.sum())\n"
        "def revenue(orders):\n"
        "    return orders.net_amount.sum()\n",
        encoding="utf-8",
    )

    load_project(project, reload=True)
    second_line = project.registry.models["sales"].metrics["revenue"].source_location.line

    assert second_line != first_line
```

- [ ] **Step 2: Run loader tests to verify failure**

Run:

```bash
make test TESTS=tests/test_semantic_py_loader.py
```

Expected: FAIL with missing `marivo.semantic_py.loader`.

- [ ] **Step 3: Implement loader with reload-safe namespace**

Create `marivo/semantic_py/loader.py`:

```python
from __future__ import annotations

import importlib
import types
import sys
from pathlib import Path

from marivo.semantic_py.errors import SemanticAssemblyError, SemanticLoadError
from marivo.semantic_py.registry import SemanticProject, use_registry
from marivo.semantic_py.validator import validate_all


def _namespace(project: SemanticProject) -> str:
    return f"_marivo_semantic_py_{abs(hash(Path(project.root).resolve()))}"


def _module_name(project: SemanticProject, file_path: Path) -> str:
    relative = file_path.relative_to(Path(project.root))
    stem = ".".join(relative.with_suffix("").parts)
    return f"{_namespace(project)}.{stem}"


def _clear_project_modules(project: SemanticProject) -> None:
    namespace = _namespace(project)
    prefix = f"{namespace}."
    for name in list(sys.modules):
        if name == namespace or name.startswith(prefix):
            del sys.modules[name]


def _semantic_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for model_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        model_file = model_dir / "_model.py"
        if not model_file.exists():
            raise SemanticLoadError(
                [
                    SemanticAssemblyError(
                        phase="load",
                        kind="ModelFileMissing",
                        location=None,
                        function=None,
                        message=f"{model_dir} must contain _model.py",
                    )
                ]
            )
        files.extend(sorted(model_dir.rglob("*.py")))
    return files


def _ensure_namespace_packages(project: SemanticProject) -> None:
    root = Path(project.root)
    namespace = _namespace(project)
    root_module = types.ModuleType(namespace)
    root_module.__path__ = [str(root)]  # type: ignore[attr-defined]
    sys.modules[namespace] = root_module

    for model_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        package_name = f"{namespace}.{model_dir.name}"
        package_module = types.ModuleType(package_name)
        package_module.__path__ = [str(model_dir)]  # type: ignore[attr-defined]
        sys.modules[package_name] = package_module


def load_project(project: SemanticProject, *, reload: bool = False) -> None:
    root = Path(project.root)
    if not root.exists():
        project.registry.state = "ready"
        return

    with project.lock:
        if reload:
            _clear_project_modules(project)
        project.registry.clear()
        project.registry.state = "loading"
        importlib.invalidate_caches()
        _ensure_namespace_packages(project)

        sys.path.insert(0, str(root))
        try:
            with use_registry(project.registry):
                for file_path in _semantic_files(root):
                    importlib.import_module(_module_name(project, file_path))
            validate_all(project.registry)
        finally:
            if sys.path and sys.path[0] == str(root):
                sys.path.pop(0)
```

- [ ] **Step 4: Run loader tests**

Run:

```bash
make test TESTS=tests/test_semantic_py_loader.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marivo/semantic_py/loader.py tests/test_semantic_py_loader.py
git commit -m "feat: load python semantic projects"
```

## Task 6: Materialization And Reader API

**Files:**
- Create: `marivo/semantic_py/reader.py`
- Modify: `marivo/semantic_py/registry.py`
- Test: `tests/test_semantic_py_materialization.py`

- [ ] **Step 1: Write materialization tests**

Create `tests/test_semantic_py_materialization.py`:

```python
from __future__ import annotations

from collections.abc import Callable

import ibis
import marivo.semantic_py as ms
from marivo.semantic_py import reader
from marivo.semantic_py.registry import SemanticProject, use_registry


def _project() -> SemanticProject:
    project = SemanticProject(root="/tmp/materialize")
    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse():
            ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend):
            return backend.table("orders")

        @ms.metric(decomposition=ms.sum())
        def revenue(orders):
            return orders.amount.sum()

    project.registry.state = "ready"
    return project


def test_materialize_metric_returns_ibis_expression() -> None:
    project = _project()
    con = ibis.duckdb.connect()
    con.create_table("orders", {"amount": [10, 20, 30]})

    expr = reader.materialize_metric(
        project=project,
        model="sales",
        metric="revenue",
        backend_factory=lambda datasource_name: con,
    )

    assert expr.execute() == 60


def test_backend_factory_is_called_once_per_datasource() -> None:
    project = _project()
    con = ibis.duckdb.connect()
    con.create_table("orders", {"amount": [7]})
    calls: list[str] = []

    def factory(datasource_name: str):
        calls.append(datasource_name)
        return con

    reader.materialize_metric(
        project=project,
        model="sales",
        metric="revenue",
        backend_factory=factory,
    ).execute()

    assert calls == ["warehouse"]
```

- [ ] **Step 2: Run materialization tests to verify failure**

Run:

```bash
make test TESTS=tests/test_semantic_py_materialization.py
```

Expected: FAIL with missing `reader.materialize_metric`.

- [ ] **Step 3: Implement reader API**

Create `marivo/semantic_py/reader.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import ibis

from marivo.semantic_py.errors import PySemanticNotFound, SemanticRuntimeError
from marivo.semantic_py.ir import DatasetIR, FieldIR, MetricIR, ModelIR
from marivo.semantic_py.loader import load_project
from marivo.semantic_py.registry import SemanticProject, default_project


def ensure_loaded(project: SemanticProject | None = None) -> None:
    target = project or default_project()
    if target.registry.state in {"unloaded", "errored"}:
        load_project(target)


def reload(project: SemanticProject | None = None) -> None:
    load_project(project or default_project(), reload=True)


def list_models(project: SemanticProject | None = None) -> list[str]:
    target = project or default_project()
    ensure_loaded(target)
    return sorted(target.registry.models)


def get_model(name: str, project: SemanticProject | None = None) -> ModelIR:
    target = project or default_project()
    ensure_loaded(target)
    try:
        return target.registry.models[name]
    except KeyError as exc:
        raise PySemanticNotFound("model", name) from exc


def get_dataset(model: str, dataset: str, project: SemanticProject | None = None) -> DatasetIR:
    model_ir = get_model(model, project)
    try:
        return model_ir.datasets[dataset]
    except KeyError as exc:
        raise PySemanticNotFound("dataset", dataset) from exc


def get_metric(model: str, metric: str, project: SemanticProject | None = None) -> MetricIR:
    model_ir = get_model(model, project)
    try:
        return model_ir.metrics[metric]
    except KeyError as exc:
        raise PySemanticNotFound("metric", metric) from exc


def get_field(
    model: str,
    dataset: str,
    field: str,
    project: SemanticProject | None = None,
) -> FieldIR:
    dataset_ir = get_dataset(model, dataset, project)
    try:
        return dataset_ir.fields[field]
    except KeyError as exc:
        raise PySemanticNotFound("field", field) from exc


def _dataset_tables(
    *,
    model_ir: ModelIR,
    dataset_names: list[str],
    backend_factory: Callable[[str], ibis.BaseBackend],
) -> dict[str, Any]:
    backends: dict[str, ibis.BaseBackend] = {}
    tables: dict[str, Any] = {}
    for dataset_name in dataset_names:
        dataset_ir = model_ir.datasets[dataset_name]
        datasource_name = dataset_ir.datasource_name
        if datasource_name not in backends:
            backends[datasource_name] = backend_factory(datasource_name)
        tables[dataset_name] = dataset_ir.fn(backends[datasource_name])
    return tables


def materialize_dataset(
    *,
    model: str,
    dataset: str,
    backend_factory: Callable[[str], ibis.BaseBackend],
    project: SemanticProject | None = None,
) -> Any:
    dataset_ir = get_dataset(model, dataset, project)
    backend = backend_factory(dataset_ir.datasource_name)
    return dataset_ir.fn(backend)


def materialize_field(
    *,
    model: str,
    dataset: str,
    field: str,
    backend_factory: Callable[[str], ibis.BaseBackend],
    project: SemanticProject | None = None,
) -> Any:
    field_ir = get_field(model, dataset, field, project)
    table = materialize_dataset(
        project=project,
        model=model,
        dataset=dataset,
        backend_factory=backend_factory,
    )
    return field_ir.fn(table)


def materialize_metric(
    *,
    model: str,
    metric: str,
    backend_factory: Callable[[str], ibis.BaseBackend],
    project: SemanticProject | None = None,
) -> Any:
    try:
        model_ir = get_model(model, project)
        metric_ir = model_ir.metrics[metric]
        tables = _dataset_tables(
            model_ir=model_ir,
            dataset_names=metric_ir.references.datasets,
            backend_factory=backend_factory,
        )
        return metric_ir.fn(**tables)
    except Exception as exc:
        if isinstance(exc, PySemanticNotFound):
            raise
        raise SemanticRuntimeError(
            phase="runtime",
            kind="MetricMaterializationFailed",
            location=None,
            function=metric,
            message=str(exc),
        ) from exc
```

- [ ] **Step 4: Run materialization tests**

Run:

```bash
make test TESTS=tests/test_semantic_py_materialization.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marivo/semantic_py/reader.py tests/test_semantic_py_materialization.py
git commit -m "feat: materialize python semantic metrics"
```

## Task 7: SQL Provenance And Parity Checks

**Files:**
- Create: `marivo/semantic_py/parity.py`
- Modify: `marivo/semantic_py/__init__.py`
- Test: `tests/test_semantic_py_parity.py`

- [ ] **Step 1: Write parity tests**

Create `tests/test_semantic_py_parity.py`:

```python
from __future__ import annotations

import ibis
import marivo.semantic_py as ms
from marivo.semantic_py.parity import compare_metric_to_source_sql
from marivo.semantic_py.registry import SemanticProject, use_registry


def _project() -> SemanticProject:
    project = SemanticProject(root="/tmp/parity")
    with use_registry(project.registry):
        ms.model(name="sales")

        @ms.datasource(name="warehouse")
        def warehouse():
            ...

        @ms.dataset(name="orders", datasource=warehouse)
        def orders(backend):
            return backend.table("orders")

        @ms.metric(
            decomposition=ms.sum(),
            source_sql="select sum(case when pay_status = 1 then amount else 0 end) as value from orders",
            source_dialect="duckdb",
            source_document="kb://finance/revenue",
        )
        def paid_revenue(orders):
            return orders.filter(orders.pay_status == 1).amount.sum()

    project.registry.state = "ready"
    return project


def test_compare_metric_to_source_sql_passes_for_equal_results() -> None:
    project = _project()
    con = ibis.duckdb.connect()
    con.create_table("orders", {"pay_status": [1, 0, 1], "amount": [10, 99, 7]})

    result = compare_metric_to_source_sql(
        project=project,
        model="sales",
        metric="paid_revenue",
        backend_factory=lambda datasource_name: con,
    )

    assert result.ok is True
    assert result.metric_value == 17
    assert result.sql_value == 17
    assert result.source_document == "kb://finance/revenue"
```

- [ ] **Step 2: Run parity tests to verify failure**

Run:

```bash
make test TESTS=tests/test_semantic_py_parity.py
```

Expected: FAIL with missing `marivo.semantic_py.parity`.

- [ ] **Step 3: Implement parity API**

Create `marivo/semantic_py/parity.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import ibis

from marivo.semantic_py import reader
from marivo.semantic_py.errors import SemanticParityError
from marivo.semantic_py.registry import SemanticProject


@dataclass(frozen=True)
class ParityResult:
    ok: bool
    metric_value: Any
    sql_value: Any
    source_sql: str
    source_dialect: str | None
    source_document: str | None


def _scalar(value: Any) -> Any:
    if hasattr(value, "iloc"):
        return value.iloc[0]
    return value


def compare_metric_to_source_sql(
    *,
    project: SemanticProject,
    model: str,
    metric: str,
    backend_factory: Callable[[str], ibis.BaseBackend],
) -> ParityResult:
    metric_ir = reader.get_metric(model, metric, project)
    if metric_ir.source is None or not metric_ir.source.sql:
        raise SemanticParityError(
            phase="parity",
            kind="SourceSqlMissing",
            location=metric_ir.source_location,
            function=metric,
            message=f"metric '{metric}' has no source_sql to compare",
        )

    metric_expr = reader.materialize_metric(
        project=project,
        model=model,
        metric=metric,
        backend_factory=backend_factory,
    )
    model_ir = reader.get_model(model, project)
    first_dataset = model_ir.datasets[metric_ir.references.datasets[0]]
    backend = backend_factory(first_dataset.datasource_name)
    sql_expr = backend.sql(metric_ir.source.sql)

    metric_value = _scalar(metric_expr.execute())
    sql_value = _scalar(sql_expr.execute()["value"])

    return ParityResult(
        ok=metric_value == sql_value,
        metric_value=metric_value,
        sql_value=sql_value,
        source_sql=metric_ir.source.sql,
        source_dialect=metric_ir.source.dialect,
        source_document=metric_ir.source.document,
    )
```

- [ ] **Step 4: Run parity tests**

Run:

```bash
make test TESTS=tests/test_semantic_py_parity.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add marivo/semantic_py/parity.py tests/test_semantic_py_parity.py
git commit -m "feat: compare semantic metrics with source SQL"
```

## Task 8: Testing Helpers And Documentation

**Files:**
- Create: `marivo/semantic_py/testing.py`
- Create: `docs/specs/semantic/python-semantic-layer.md`
- Test: `tests/test_semantic_py_decorators.py`

- [ ] **Step 1: Add testing helper coverage**

Append to `tests/test_semantic_py_decorators.py`:

```python
from marivo.semantic_py.testing import scoped_project


def test_scoped_project_restores_registry_after_exit() -> None:
    outer = SemanticProject(root="/tmp/outer")
    with use_registry(outer.registry):
        ms.model(name="outer")
        with scoped_project(root="/tmp/inner") as inner:
            ms.model(name="inner")
            assert sorted(inner.registry.models) == ["inner"]
        assert sorted(outer.registry.models) == ["outer"]
```

- [ ] **Step 2: Run helper test to verify failure**

Run:

```bash
make test TESTS=tests/test_semantic_py_decorators.py::test_scoped_project_restores_registry_after_exit
```

Expected: FAIL with missing `marivo.semantic_py.testing`.

- [ ] **Step 3: Implement testing helper**

Create `marivo/semantic_py/testing.py`:

```python
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from marivo.semantic_py.registry import SemanticProject, use_registry


@contextmanager
def scoped_project(root: str = "/tmp/marivo-semantic-py-test") -> Iterator[SemanticProject]:
    project = SemanticProject(root=root)
    with use_registry(project.registry):
        yield project
```

- [ ] **Step 4: Create public documentation**

Create `docs/specs/semantic/python-semantic-layer.md`:

````markdown
# Python Semantic Layer

`marivo.semantic_py` lets agentic workers define semantic models in Python files.
Python definitions are trusted local code and are loaded from a semantic project root.

## Minimal Model

```python
import marivo.semantic_py as ms

ms.model(name="sales", description="Sales analytics")

@ms.datasource(name="warehouse", backend_type="duckdb")
def warehouse():
    ...

@ms.dataset(name="orders", datasource=warehouse, primary_key=["order_id"])
def orders(backend):
    return backend.table("orders")

@ms.time_field(dataset="orders", data_type="date", granularity="day")
def order_date(orders):
    return orders.created_at.cast("date")

@ms.metric(
    decomposition=ms.sum(),
    source_sql="select sum(amount) as value from orders",
    source_dialect="duckdb",
    source_document="kb://sales/revenue",
)
def revenue(orders):
    return orders.amount.sum()
```

## SQL Provenance

Business metric definitions often come from SQL knowledge bases. Store the source SQL on the metric using `source_sql`, `source_dialect`, and `source_document`, then compare the Python/ibis expression against the SQL on a bounded fixture or sample window before trusting the translated metric.

## Validation

Run targeted tests while developing:

```bash
make test TESTS=tests/test_semantic_py_decorators.py
make test TESTS=tests/test_semantic_py_materialization.py
make test TESTS=tests/test_semantic_py_parity.py
```
````

- [ ] **Step 5: Run helper and docs-adjacent tests**

Run:

```bash
make test TESTS=tests/test_semantic_py_decorators.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add marivo/semantic_py/testing.py docs/specs/semantic/python-semantic-layer.md tests/test_semantic_py_decorators.py
git commit -m "docs: document python semantic layer"
```

## Task 9: Quality Gates And Self-Review

**Files:**
- Modify only files touched by prior tasks if verification reveals issues.

- [ ] **Step 1: Run focused semantic_py test suite**

Run:

```bash
make test TESTS="tests/test_semantic_py_ir.py tests/test_semantic_py_decorators.py tests/test_semantic_py_validation.py tests/test_semantic_py_loader.py tests/test_semantic_py_materialization.py tests/test_semantic_py_parity.py"
```

Expected: all selected tests PASS.

- [ ] **Step 2: Run formatting**

Run:

```bash
make format
```

Expected: command exits 0. Restage any files changed by formatter before committing.

- [ ] **Step 3: Run typecheck**

Run:

```bash
make typecheck
```

Expected: command exits 0. If mypy reports `type: ignore` issues in `decorators.py`, replace broad ignores with local helper functions that return typed `Literal` values.

- [ ] **Step 4: Run lint**

Run:

```bash
make lint
```

Expected: command exits 0.

- [ ] **Step 5: Run full tests if focused checks pass**

Run:

```bash
make test
```

Expected: full test suite PASS.

- [ ] **Step 6: Review git diff**

Run:

```bash
git diff --stat
git diff -- marivo/semantic_py tests/test_semantic_py_ir.py tests/test_semantic_py_decorators.py tests/test_semantic_py_validation.py tests/test_semantic_py_loader.py tests/test_semantic_py_materialization.py tests/test_semantic_py_parity.py docs/specs/semantic/python-semantic-layer.md pyproject.toml
```

Expected: diff only contains the Python semantic layer package, tests, docs, and dependency change.

- [ ] **Step 7: Final commit**

```bash
git add pyproject.toml marivo/semantic_py tests/test_semantic_py_*.py docs/specs/semantic/python-semantic-layer.md
git commit -m "feat: add python semantic layer dsl"
```

## Self-Review Notes

- Spec coverage: the plan covers the public API, datasource identity, dataset/field/time-field/metric/relationship declarations, lazy project loading, reload, registry read APIs, materialization, testing helpers, and docs.
- Review-risk coverage: the plan adds `source_sql`/`source_dialect`/`source_document`, parity comparison, forward refs with `ms.ref(...)`, project-scoped registry state, and reload-safe module clearing.
- Deliberately deferred: Python operation DSL, HTTP/MCP replacement, typed intent replacement, SQL compiler replacement, file watching, sandboxing for untrusted Python, and cross-project model discovery. Those need separate specs and plans because they are independent subsystems.
