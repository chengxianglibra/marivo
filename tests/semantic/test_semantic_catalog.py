"""Tests for marivo.semantic.catalog — SemanticCatalog public API."""

from __future__ import annotations

import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

import ibis
import pytest
from ibis.common.deferred import Deferred

import marivo.datasource as md
import marivo.semantic as ms
from marivo._definition.expressions import DeclaredMetricRef
from marivo.datasource.snapshot import DiscoverySnapshot
from marivo.refs import (
    DimensionKind,
    MeasureKind,
    Ref,
    SemanticKindTag,
)
from marivo.refs import ref as ref_factory
from marivo.semantic._deferred_semantics import consume_field, consume_metric
from marivo.semantic.catalog import (
    AiContextView,
    SemanticCatalog,
    SemanticKind,
)
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.ir import SourceLocation
from tests.shared_fixtures import catalog_value

if TYPE_CHECKING:
    from ibis.backends.duckdb import Backend

    from tests.conftest import _SemanticProjectFactory


# --- SemanticKind ---


def test_semantic_kind_is_symbol_kind_alias() -> None:
    assert SemanticKind is ms.SemanticKind


_FACTORY_BY_KIND: dict[SemanticKind, Callable[[str], Ref[SemanticKindTag]]] = {
    kind: cast("Callable[[str], Ref[SemanticKindTag]]", factory)
    for kind, factory in {
        SemanticKind.DOMAIN: ref_factory.domain,
        SemanticKind.DATASOURCE: ref_factory.datasource,
        SemanticKind.ENTITY: ref_factory.entity,
        SemanticKind.DIMENSION: ref_factory.dimension,
        SemanticKind.TIME_DIMENSION: ref_factory.time_dimension,
        SemanticKind.MEASURE: ref_factory.measure,
        SemanticKind.METRIC: ref_factory.metric,
        SemanticKind.RELATIONSHIP: ref_factory.relationship,
    }.items()
}


def make_ref(path: str, kind: SemanticKind) -> Ref[SemanticKindTag]:
    return _FACTORY_BY_KIND[kind](path)


def _make_ref(r: str, kind: SemanticKind) -> Ref[SemanticKindTag]:
    return make_ref(r, kind)


def _ref_from_typed_id_path(value: str) -> Ref[SemanticKindTag]:
    kind_value, separator, path = value.partition(".")
    assert separator
    return make_ref(path, SemanticKind(kind_value))


def _make_ctx() -> AiContextView:
    return AiContextView(
        business_definition="Revenue from completed orders.",
        guardrails=("Exclude refunds.",),
    )


def _make_loc() -> SourceLocation:
    return SourceLocation(file="models/semantic/sales/_domain.py", line=5)


# --- Kind-specific details ---


# --- Fixtures ---


_MINIMAL_DOMAIN_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
""")

_DATASETS_PY = textwrap.dedent("""    import marivo.datasource as md
    import marivo.semantic as ms

    orders = ms.entity(
        name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders")
    )
    region = ms.dimension_column(name="region", entity=orders, column="region")
    created_at = ms.time_dimension_column(
        name="created_at",
        entity=orders,
        column="created_at",
        parse=ms.timestamp(timezone="UTC"),
    )
    amount = ms.measure_column(
        name="amount", entity=orders, column="amount", additivity=ms.additive_all()
    )

    amount_value = amount
    created_at_value = created_at

    @ms.metric()
    def revenue():
        return amount_value.sum(time=created_at_value)
""")

_RICH_DETAILS_DATASETS_PY = textwrap.dedent("""    import marivo.datasource as md
    import marivo.semantic as ms

    orders = ms.entity(
        name="orders",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table("orders"),
        ai_context=ms.ai_context(
            business_definition="One row per completed order.",
            guardrails=["Exclude test orders."],
        ),
    )
    region = ms.dimension_column(
        name="region",
        entity=orders,
        column="region",
        ai_context=ms.ai_context(
            business_definition="Region assigned to the completed order.",
            guardrails=["Do not infer sales ownership from region alone."],
        ),
    )
    amount = ms.measure_column(
        name="amount",
        entity=orders,
        column="amount",
        additivity=ms.additive_all(),
        unit="USD",
        ai_context=ms.ai_context(
            business_definition="Gross order amount before refunds.",
            guardrails=["Does not net out refunds."],
        ),
    )
    created_at = ms.time_dimension_column(
        name="created_at",
        entity=orders,
        column="created_at",
        parse=ms.timestamp(timezone="UTC"),
        ai_context=ms.ai_context(
            business_definition="Order creation timestamp.",
            guardrails=["Do not use as payment settlement time."],
        ),
    )

    amount_value = amount
    created_at_value = created_at

    @ms.metric(
        ai_context=ms.ai_context(
            business_definition="Total gross order amount before refunds.",
            guardrails=["Do not use as net revenue."],
        ),
    )
    def revenue():
        return amount_value.sum(time=created_at_value)
""")


def _make_catalog(semantic_project_factory: _SemanticProjectFactory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    return SemanticCatalog(project)


def _make_rich_catalog(semantic_project_factory: _SemanticProjectFactory) -> SemanticCatalog:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _RICH_DETAILS_DATASETS_PY,
        }
    )
    return SemanticCatalog(project)


# --- Collection selection returns entries or expression values ---


# --- Agent result protocol ---


# --- Top-level typed collections ---


# --- Collection lookup ---


def test_catalog_get_no_stdout(
    semantic_project_factory: _SemanticProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    catalog_value(catalog, "sales.revenue", kind="metric")
    _ = catalog.get("dimension:sales.orders.region").definition
    assert capsys.readouterr().out == ""


# --- ms.load() ---


def test_ms_load_defaults_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_minimal_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    catalog = ms.load()
    assert {ref.path for ref in [entry._ref for entry in catalog.search(kind="metric").items]} == {
        "sales.revenue"
    }


def test_ms_load_failure_raises_semantic_load_error(tmp_path: Path) -> None:
    semantic = tmp_path / "models" / "semantic" / "sales"
    semantic.mkdir(parents=True)
    (semantic / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='wrong_name', owner='Mina Zhang')\n"
    )
    from marivo.semantic.errors import SemanticLoadFailed

    with pytest.raises(SemanticLoadFailed):
        ms.load(workspace_dir=tmp_path)


def test_ms_load_does_not_print(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _write_minimal_project(tmp_path)
    ms.load(workspace_dir=tmp_path)
    assert capsys.readouterr().out == ""


def test_ms_load_catalog_can_browse(tmp_path: Path) -> None:
    _write_minimal_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path)
    result = catalog.search(kind="domain")
    assert len(result.items) >= 1
    assert "sales.revenue" in {
        ref.path for ref in [entry._ref for entry in catalog.search(kind="metric").items]
    }


def test_ms_load_with_domains_filters_domains_and_accepts_string(tmp_path: Path) -> None:
    """ms.load(domains=...) filters to the specified domain directories."""
    _write_multi_domain_project(tmp_path)
    catalog = ms.load(workspace_dir=tmp_path, domains=["sales"])
    refs = {obj._ref.path for obj in catalog.search(kind="domain").items}
    assert "sales" in refs
    assert "ops" not in refs

    string_catalog = ms.load(workspace_dir=tmp_path, domains="sales")
    string_refs = {obj._ref.path for obj in string_catalog.search(kind="domain").items}
    assert "sales" in string_refs
    assert "ops" not in string_refs


def test_catalog_lifecycle_properties_delegate_to_project(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    catalog = SemanticCatalog(project)

    assert catalog.semantic_root == project.semantic_root
    assert catalog.workspace_dir == project.workspace_dir


def test_ms_load_returns_fresh_catalog_without_mutating_existing_catalog(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    project = semantic_project_factory(
        {"sales/_domain.py": _MINIMAL_DOMAIN_PY, "sales/datasets.py": _DATASETS_PY}
    )
    catalog = SemanticCatalog(project)
    (project.semantic_root / "sales" / "profit.py").write_text(
        textwrap.dedent(
            "            import marivo.semantic as ms\n            from .datasets import amount_value, created_at_value\n\n            @ms.metric()\n            def profit():\n                return amount_value.sum(time=created_at_value) * 2\n        "
        )
    )
    with pytest.raises(SemanticRuntimeError):
        catalog.get("metric:sales.profit")
    reloaded = ms.load(workspace_dir=project.workspace_dir)
    with pytest.raises(SemanticRuntimeError):
        catalog.get("metric:sales.profit")
    profit = catalog_value(reloaded, "sales.profit", kind="metric")
    node = consume_metric(profit).node
    assert isinstance(node, DeclaredMetricRef)
    assert node.ref.ref.path == "sales.profit"
    assert reloaded is not catalog


def test_ms_load_can_recreate_filtered_domain_scope(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'))\n"
            ),
        },
        load=False,
    )
    project.load("sales")
    SemanticCatalog(project)

    reloaded = ms.load(workspace_dir=project.workspace_dir, domains="sales")

    refs = {obj._ref.path for obj in reloaded.search(kind="domain").items}
    assert "sales" in refs
    assert "ops" not in refs


def test_ms_load_with_domains_returns_different_filtered_catalog(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
            "ops/_domain.py": "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n",
            "ops/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'))\n"
            ),
        },
        load=False,
    )
    project.load("sales")
    catalog = SemanticCatalog(project)

    reloaded = ms.load(workspace_dir=project.workspace_dir, domains="ops")

    refs = {obj._ref.path for obj in reloaded.search(kind="domain").items}
    assert "ops" in refs
    assert "sales" not in refs
    assert {obj._ref.path for obj in catalog.search(kind="domain").items} == {"sales"}


# --- catalog.preview() ---


def _preview_backend(path: str) -> Backend:
    backend = ibis.duckdb.connect(path)
    backend.con.execute(
        "CREATE TABLE orders (order_id INT, amount DOUBLE, region TEXT, created_at TIMESTAMP)"
    )
    backend.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-01-02')"
    )
    return backend


def _preview_catalog_and_snapshot(
    semantic_project_factory: _SemanticProjectFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[SemanticCatalog, DiscoverySnapshot]:
    database_path = tmp_path / "warehouse.duckdb"
    backend = _preview_backend(str(database_path))
    backend.disconnect()
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": (
                "import marivo.datasource as md\n"
                f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
            ),
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _DATASETS_PY,
        }
    )
    monkeypatch.chdir(tmp_path)
    catalog = SemanticCatalog(project)
    snapshot = md.inspect(ms.ref.datasource("warehouse"), md.table("orders")).sample(
        scope=md.unpruned(max_rows=2, timeout_seconds=30),
        columns=("order_id", "amount", "region", "created_at"),
    )
    return catalog, snapshot


def test_catalog_preview_field_published_metric_and_context_boundaries(
    semantic_project_factory: _SemanticProjectFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog, snapshot = _preview_catalog_and_snapshot(
        semantic_project_factory, tmp_path, monkeypatch
    )
    field_preview = catalog.preview(
        catalog.get("dimension:sales.orders.region"),
        scope=snapshot.scope,
        context_columns=("order_id",),
        limit=2,
    )
    assert field_preview.ref == "sales.orders.region"
    assert field_preview.columns[:2] == ("order_id", "region")
    published = catalog.preview(
        catalog_value(catalog, "sales.revenue", kind="metric"), scope=snapshot.scope, limit=2
    )
    assert published.kind == "semantic_metric"
    assert published.rows == ({"value": 300.0},)
    assert published.sample_policy.method == "pre_aggregate_limit"
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.preview(
            catalog.get("entity:sales.orders"), scope=snapshot.scope, context_columns=("order_id",)
        )
    assert exc_info.value.kind == ErrorKind.MATERIALIZE_FAILED
    assert "context_columns" in str(exc_info.value)


def _write_minimal_project(tmp_path: Path) -> None:
    semantic = tmp_path / "models" / "semantic" / "sales"
    ds = tmp_path / "models" / "datasources"
    semantic.mkdir(parents=True)
    ds.mkdir(parents=True)
    (ds / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang', default=True)\n"
    )
    (semantic / "datasets.py").write_text(_SALES_REVENUE_PY)


_SALES_REVENUE_PY = (
    "import marivo.datasource as md\nimport marivo.semantic as ms\n"
    "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
    "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
    "\n"
    ""
    "amount_value = amount\n"
    "\n"
    "@ms.metric()\n"
    "def revenue():\n"
    "    return amount_value.sum()\n"
)


def _write_multi_domain_project(tmp_path: Path) -> None:
    """Write a project with both 'sales' and 'ops' domains."""
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    ds = tmp_path / "models" / "datasources"
    ds.mkdir(parents=True, exist_ok=True)
    (ds / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    sales = tmp_path / "models" / "semantic" / "sales"
    sales.mkdir(parents=True, exist_ok=True)
    (sales / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang', default=True)\n"
    )
    (sales / "datasets.py").write_text(_SALES_REVENUE_PY)
    ops = tmp_path / "models" / "semantic" / "ops"
    ops.mkdir(parents=True, exist_ok=True)
    (ops / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='ops', owner='Mina Zhang')\n"
    )
    (ops / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'))\n"
    )


def _measure_ref(field: Deferred) -> Ref[MeasureKind]:
    reference = consume_field(field).reference.ref
    assert reference.kind is SemanticKind.MEASURE
    return ref_factory.measure(reference.path)


def _dimension_ref(field: Deferred) -> Ref[DimensionKind]:
    reference = consume_field(field).reference.ref
    assert reference.kind is SemanticKind.DIMENSION
    return ref_factory.dimension(reference.path)


# --- catalog.readiness() ---


def test_catalog_readiness_normalizes_named_objects_to_same_public_payload(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    sales = catalog.get("domain:sales")
    region = catalog.get("dimension:sales.orders.region")
    by_domain = catalog.readiness(refs=[sales])
    by_repeated_domain = catalog.readiness(refs=[catalog.get(sales.id)])
    by_field = catalog.readiness(refs=[region])
    by_repeated_field = catalog.readiness(refs=[catalog.get(region.id)])
    assert by_domain.requested_ids == by_repeated_domain.requested_ids == (sales.id,)
    assert by_domain.input_summary == by_repeated_domain.input_summary
    assert by_field.requested_ids == by_repeated_field.requested_ids == (region.id,)
    assert by_field.input_summary == by_repeated_field.input_summary
    assert tuple(value.id for value in by_field.analysis_ready_inputs) == (region.id,)
    assert isinstance(by_field.analysis_ready_inputs[0], ms.Dimension)
    with pytest.raises(SemanticRuntimeError, match="unique exact refs"):
        catalog.readiness(refs=[sales, catalog.get(sales.id)])


@pytest.mark.parametrize(
    "selector", (ms.ref.domain("sales"), ms.ref.dimension("sales.orders.region"))
)
def test_catalog_readiness_rejects_authoring_selectors(
    semantic_project_factory: _SemanticProjectFactory, selector: Deferred
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as caught:
        catalog.readiness(refs=[selector])  # type: ignore[list-item]
    assert caught.value.kind == ErrorKind.INVALID_REF
    assert caught.value.details["query_executed"] is False
    assert "named semantic object" in (caught.value.expected or "")


def test_catalog_readiness_preserves_exact_kind_when_paths_collide(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    project = semantic_project_factory(
        {
            "datasources/sales.py": (
                "import marivo.datasource as md\nmd.duckdb(name='sales', path=':memory:')\n"
            ),
            "sales/_domain.py": (
                "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
            ),
        }
    )
    catalog = SemanticCatalog(project)

    domain_report = catalog.readiness(refs=[catalog.get("domain:sales")])
    datasource_report = catalog.readiness(refs=[catalog.get("datasource:sales")])
    all_report = catalog.readiness()

    assert domain_report._analysis_ready_inputs == (ref_factory.domain("sales"),)
    assert datasource_report._analysis_ready_inputs == (ref_factory.datasource("sales"),)
    assert set(all_report._analysis_ready_inputs) == {
        ref_factory.datasource("default"),
        ref_factory.domain("sales"),
        ref_factory.datasource("sales"),
    }


def test_catalog_readiness_rejects_string_refs(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.readiness(refs=["sales.revenue"])  # type: ignore[list-item]
    error = exc_info.value
    assert error.kind == ErrorKind.INVALID_REF
    assert "named semantic object" in (error.expected or "")
    assert error.received == "str"
    assert error.repair is not None
    assert error.repair.help_target.surface == "semantic"
    assert error.repair.help_target.canonical_id == "readiness"
    assert "metric:sales.revenue" in error.repair.candidates
    assert all(catalog.get(candidate).id == candidate for candidate in error.repair.candidates)
    assert error.details["query_executed"] is False
    rendered = str(error)
    assert "named semantic object" in rendered
    assert "expected: a named semantic object" in rendered
    assert "received: str" in rendered

    with pytest.raises(SemanticRuntimeError) as unknown_exc_info:
        catalog.readiness(refs=["sales.missing"])  # type: ignore[list-item]
    unknown_repair = unknown_exc_info.value.repair
    assert unknown_repair is not None
    assert all(catalog.get(candidate).id == candidate for candidate in unknown_repair.candidates)
    assert unknown_repair.snippet is None


def test_catalog_readiness_non_string_error_does_not_claim_string_input(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)

    with pytest.raises(SemanticRuntimeError) as exc_info:
        catalog.readiness(refs=[42])  # type: ignore[list-item]

    error = exc_info.value
    assert error.received == "int"
    assert "received: int" in str(error)
    assert "bare strings are not accepted" not in str(error)
    assert error.repair is not None
    assert all(catalog.get(candidate).id == candidate for candidate in error.repair.candidates)
    assert error.repair.snippet is None


def test_catalog_readiness_rejects_empty_or_duplicate_explicit_scope(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    orders = catalog.get("entity:sales.orders")
    with pytest.raises(SemanticRuntimeError, match="non-empty"):
        catalog.readiness(refs=[])
    with pytest.raises(SemanticRuntimeError, match="unique exact refs") as exc_info:
        catalog.readiness(refs=[orders, orders])
    assert exc_info.value.semantic_refs == ("entity:sales.orders",)


def test_catalog_readiness_no_stdout(
    semantic_project_factory: _SemanticProjectFactory, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _make_catalog(semantic_project_factory)
    catalog.readiness()
    assert capsys.readouterr().out == ""


def test_dimension_ref_rejects_domain_level_path(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    del semantic_project_factory
    with pytest.raises(ValueError, match="exactly 3 segments"):
        ms.ref.dimension("sales.region")


# --- metric unit passthrough ---


_UNIT_DATASETS_PY = (
    "import marivo.datasource as md\nimport marivo.semantic as ms\n"
    "\n"
    "warehouse = ms.ref.datasource('warehouse')\n"
    "\n"
    "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
    "amount = ms.measure_column(name='amount', entity=orders, column='amount', "
    "additivity=ms.additive_all(), unit='CNY')\n"
    "margin = ms.measure_column(name='margin', entity=orders, column='margin', "
    "additivity=ms.additive_all())\n"
    "\n"
    ""
    "amount_value = amount\n"
    "margin_value = margin\n"
    "\n"
    "@ms.metric()\n"
    "def revenue():\n"
    "    return amount_value.sum()\n"
    "\n"
    "@ms.metric()\n"
    "def gross_margin():\n"
    "    return margin_value.sum()\n"
)

_WAREHOUSE_PY = "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"


def test_catalog_metric_values_preserve_declared_and_absent_units(
    semantic_project_factory: _SemanticProjectFactory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _MINIMAL_DOMAIN_PY,
            "sales/datasets.py": _UNIT_DATASETS_PY,
            "datasources/warehouse.py": _WAREHOUSE_PY,
        }
    )
    catalog = SemanticCatalog(project)
    revenue = catalog_value(catalog, "sales.revenue", kind="metric")
    margin = catalog_value(catalog, "sales.gross_margin", kind="metric")
    assert isinstance(revenue, ms.Metric)
    assert revenue.definition.unit == "CNY"
    assert catalog.get("metric:sales.revenue").definition.unit == "CNY"
    assert isinstance(margin, ms.Metric)
    assert margin.definition.unit is None
    assert catalog.get("metric:sales.gross_margin").definition.unit is None
