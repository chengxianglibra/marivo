"""Execute the authoring examples published by the public Help coordinator."""

from __future__ import annotations

import re
import textwrap
from collections.abc import Callable, Iterator
from pathlib import Path

import ibis
import pytest
from ibis.backends.duckdb import Backend

import marivo
import marivo.datasource as md
import marivo.semantic as ms
from marivo._help.render import PublicHelpTarget
from marivo._help.render import help as public_help
from marivo.preview import PreviewResult
from marivo.refs import ref as ref_factory
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind
from marivo.semantic.expressions import Entity
from marivo.semantic.materializer import Materializer
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import patch_semantic_connections
from tests.support.documentation import _example as documentation_example

ProjectFactory = Callable[[dict[str, str]], SemanticProject]

_IMPORTS = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
_ORDERS = (
    _IMPORTS + "orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), "
    "source=md.table('orders'))\n"
)


@pytest.mark.parametrize("edition", ("docs", "zh-cn/docs"))
def test_modeling_tutorial_capture_sequence(
    semantic_project_factory: ProjectFactory, edition: str
) -> None:
    source = documentation_example(edition, "semantic-model", page="modeling/semantic-layer")
    project = semantic_project_factory({"sales/_domain.py": source})
    catalog = SemanticCatalog(project)
    from marivo.semantic._deferred_semantics import consume_field

    value = catalog.get("dimension:sales.orders.normalized_region").value
    assert consume_field(value).reference.ref == ref_factory.dimension(
        "sales.orders.normalized_region"
    )
    normalized = catalog._state.sidecar.bodies[
        ref_factory.dimension("sales.orders.normalized_region")
    ]
    assert normalized.bindings[0].to_ref() == ref_factory.dimension("sales.orders.region")
    assert (
        catalog._state.registry.dimensions["sales.orders.normalized_region"].entity
        == "sales.orders"
    )


def _help_text(target: PublicHelpTarget, capsys: pytest.CaptureFixture[str]) -> str:
    assert marivo.help is public_help
    public_help(target)
    return capsys.readouterr().out


def _example(target: str, capsys: pytest.CaptureFixture[str]) -> str:
    output = _help_text(target, capsys)
    assert "  Example:\n" in output
    lines: list[str] = []
    for line in output.split("  Example:\n", 1)[1].splitlines():
        if line and not line.startswith("    "):
            break
        lines.append(line)
    return textwrap.dedent("\n".join(lines)) + "\n"


def _project(factory: ProjectFactory, source: str) -> SemanticProject:
    project = factory(
        {
            "sales/_domain.py": _IMPORTS
            + "ms.domain(name='sales', owner='Mina Zhang', default=True)\n",
            "sales/model.py": source,
        }
    )
    return project


@pytest.fixture
def orders_backend() -> Iterator[Backend]:
    backend = ibis.duckdb.connect(":memory:")
    backend.con.execute(
        "CREATE TABLE orders (id BIGINT, total DOUBLE, price DOUBLE, quantity BIGINT, "
        "amount DOUBLE, state VARCHAR, region VARCHAR)"
    )
    backend.con.execute(
        "INSERT INTO orders VALUES "
        "(1, 20, 10, 2, 999, 'FAILED', 'US'), "
        "(2, 21, 7, 3, 999, 'ERROR', 'CA'), "
        "(3, 5, 5, 1, 999, 'OK', 'US')"
    )
    try:
        yield backend
    finally:
        backend.disconnect()


def test_metric_help_executes_the_declared_computed_measure(
    semantic_project_factory: ProjectFactory,
    orders_backend: Backend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _project(
        semantic_project_factory, _ORDERS + _example("semantic.metric.define", capsys)
    )
    assert project.is_ready(), project.errors()
    assert orders_backend.table("orders").amount.sum().execute() == 2997
    with patch_semantic_connections(project, lambda _datasource: orders_backend):
        assert SemanticCatalog(project).preview(
            ms.ref.metric("sales.revenue"), scope=md.unpruned(max_rows=10, timeout_seconds=30)
        ).rows == ({"value": 46},)
    parents = SemanticCatalog(project)._state.dependencies[ref_factory.metric("sales.revenue")]
    assert ref_factory.measure("sales.orders.quantity") in parents
    assert ref_factory.measure("sales.orders.unit_price") in parents


def test_bind_help_executes_cross_file_fields_before_their_declaration_file(
    semantic_project_factory: ProjectFactory,
    orders_backend: Backend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = semantic_project_factory(
        {
            "trino_query/_domain.py": _IMPORTS
            + "ms.domain(name='trino_query', owner='Mina Zhang', default=True)\n",
            "trino_query/a_consumer.py": _IMPORTS
            + "orders = ms.ref.entity('trino_query.trino_query_info')\n"
            + "amount = ms.ref.measure('trino_query.trino_query_info.amount')\n"
            + "query_info = orders\n"
            + _example("semantic.bind", capsys),
            "trino_query/z_fields.py": _IMPORTS + "@ms.entity("
            "datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
            "def trino_query_info(raw):\n    return raw.mutate(amount=raw.price * raw.quantity)\n"
            "query_info = trino_query_info\n"
            "@ms.dimension(entity=query_info)\n"
            "def state(rows):\n    return rows.state.upper()\n"
            "amount = ms.measure_column(name='amount', entity=query_info, column='amount', additivity=ms.additive_all())\n",
        }
    )
    assert project.is_ready(), project.errors()
    orders_backend.con.execute("UPDATE orders SET state=lower(state)")
    materializer = Materializer(project, lambda _datasource: orders_backend)
    assert orders_backend.execute(
        materializer.dimension("trino_query.trino_query_info.has_amount")
    ).tolist() == [True, True, True]
    assert (
        orders_backend.execute(materializer.measure("trino_query.trino_query_info.amount")).sum()
        == 46
    )


def test_binding_to_an_absent_field_names_the_path_and_routes_to_bind(
    semantic_project_factory: ProjectFactory,
) -> None:
    source = _ORDERS + (
        "state = ms.ref.dimension('sales.orders.missing_state')\n"
        "@ms.dimension(entity=orders)\n"
        "def failed(rows):\n    return (ms.bind(state, rows) == 'FAILED').cast('int64').sum()\n"
    )
    project = _project(semantic_project_factory, source)
    error = next(
        error for error in project.errors() if error.kind == ErrorKind.BINDING_TARGET_MISSING
    )
    assert "sales.orders.missing_state" in error.message
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "bind"


@pytest.mark.parametrize(
    ("body", "kind", "received"),
    (
        (
            "return ms.bind(rows.amount, rows).sum()",
            ErrorKind.BINDING_ALIAS_NOT_DIRECT,
            "rows.amount",
        ),
        (
            "return ms.bind(factory('x'), rows).sum()",
            ErrorKind.BINDING_ALIAS_NOT_DIRECT,
            "factory(",
        ),
        (
            "return ms.bind(ms.ref.measure('sales.orders.amount'), rows).sum()",
            ErrorKind.BINDING_ALIAS_NOT_DIRECT,
            "ms.ref.measure(",
        ),
        ("return ms.bind(missing, rows).sum()", ErrorKind.INVALID_BINDING_REF, "missing: NoneType"),
        ("return ms.bind(orders, rows).sum()", ErrorKind.INVALID_BINDING_REF, "orders: entity"),
        (
            "return ms.bind(amount, rows.filter(rows.price > 0)).sum()",
            ErrorKind.BINDING_ALIAS_NOT_DIRECT,
            "filter",
        ),
        (
            "alias = rows.view()\n    return ms.bind(amount, alias).sum()",
            ErrorKind.BINDING_ALIAS_NOT_DIRECT,
            "alias",
        ),
    ),
)
def test_binding_repair_identifies_the_bad_argument_and_resolves_to_bind_help(
    semantic_project_factory: ProjectFactory,
    capsys: pytest.CaptureFixture[str],
    body: str,
    kind: ErrorKind,
    received: str,
) -> None:
    project = _project(
        semantic_project_factory,
        _ORDERS + "amount = ms.measure_column(name='amount', entity=orders, column='amount', "
        "additivity=ms.additive_all())\n"
        "@ms.dimension(entity=orders)\n"
        f"def revenue(rows):\n    {body}\n",
    )
    assert not project.is_ready()
    error = next(error for error in project.errors() if error.kind == kind)
    assert error.received is not None and received in error.received
    assert error.expected and error.hint
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "bind"
    target = error.repair.help_target
    assert "Entrypoint: ms.bind" in _help_text(f"{target.surface}.{target.canonical_id}", capsys)


@pytest.mark.parametrize(
    ("target", "metric", "expected"),
    (
        ("count", "failed", 1),
        ("where", "failed", 1),
        ("aggregate", "us_revenue", 1998),
        ("membership", "selected", 2),
    ),
)
def test_filter_help_examples_declare_their_local_dimensions_and_execute(
    semantic_project_factory: ProjectFactory,
    orders_backend: Backend,
    capsys: pytest.CaptureFixture[str],
    target: str,
    metric: str,
    expected: int,
) -> None:
    canonical = {
        "count": "entity.count",
        "where": "metric.where",
        "aggregate": "row.sum",
        "membership": "field.isin",
    }[target]
    source = _ORDERS + "paid_at = None\n"
    source += "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
    source += "status = ms.dimension_column(name='status', entity=orders, column='state')\n"
    source += "region = ms.dimension_column(name='region', entity=orders, column='region')\n"
    source += "orders = orders\namount = amount\nstatus = status\nregion = region\n"
    source += "revenue = orders.count()\n"
    expression = _example(f"semantic.{canonical}", capsys).strip().replace("'paid'", "'FAILED'")
    if target == "count":
        expression += ".where(status == 'FAILED')"
    elif target == "aggregate":
        expression += ".where(region == 'US')"
    elif target == "membership":
        expression = f"orders.count().where(({expression}) & ~(status == 'OK'))"
    source += f"@ms.metric()\ndef {metric}():\n    return {expression}\n"
    project = _project(semantic_project_factory, source)
    assert project.is_ready(), project.errors()
    with patch_semantic_connections(project, lambda _datasource: orders_backend):
        assert SemanticCatalog(project).preview(
            ms.ref.metric(f"sales.{metric}"), scope=md.unpruned(max_rows=10, timeout_seconds=30)
        ).rows == ({"value": expected},)


def test_entity_help_projection_gets_types_and_values_from_the_physical_source(
    semantic_project_factory: ProjectFactory,
    orders_backend: Backend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _IMPORTS + _example("semantic.entity.define", capsys)
    source += "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
    project = _project(semantic_project_factory, source)
    assert project.is_ready(), project.errors()
    materializer = Materializer(project, lambda _datasource: orders_backend)
    expression = materializer.measure("sales.orders.amount")
    assert str(expression.type()) == "float64"
    assert orders_backend.execute(expression).tolist() == [20, 21, 5]


@pytest.mark.parametrize("builder", ("count", "aggregate"))
def test_filter_keywords_cannot_name_dimensions_owned_by_another_entity(
    semantic_project_factory: ProjectFactory, builder: str
) -> None:
    source = _ORDERS + (
        "users = ms.entity(name='users', datasource=ms.ref.datasource('warehouse'), source=md.table('users'))\n"
        "state = ms.dimension_column(name='state', entity=users, column='state')\n"
        "amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())\n"
    )
    source += "orders = orders\namount = amount\nstate = state\n"
    expression = "orders.count()" if builder == "count" else "amount.sum()"
    source += f"@ms.metric()\ndef failed():\n    return {expression}.where(state == 'FAILED')\n"
    project = _project(semantic_project_factory, source)
    assert not project.is_ready()
    error = next(error for error in project.errors() if error.kind == ErrorKind.INVALID_WHERE_SCOPE)
    assert error.repair is not None
    assert "another" in error.message


def test_preview_help_example_executes_with_its_linked_scope_constructor(
    semantic_project_factory: ProjectFactory,
    orders_backend: Backend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "warehouse.duckdb"
    backend = ibis.duckdb.connect(str(database_path))
    try:
        backend.create_table("orders", orders_backend.table("orders").to_pandas())
    finally:
        backend.disconnect()
    project = semantic_project_factory(
        {
            "datasources/warehouse.py": _IMPORTS
            + f"md.duckdb(name='warehouse', path={str(database_path)!r})\n",
            "sales/_domain.py": _IMPORTS
            + "ms.domain(name='sales', owner='Mina Zhang', default=True)\n",
            "sales/model.py": _ORDERS + _example("semantic.metric.define", capsys),
        }
    )
    assert project.is_ready(), project.errors()
    monkeypatch.chdir(tmp_path)
    catalog = SemanticCatalog(project)
    namespace: dict[str, object] = {
        "catalog": catalog,
        "revenue": ms.ref.metric("sales.revenue"),
        "md": md,
    }
    example = _example("semantic.preview", capsys)
    exec(compile("preview = " + example, "semantic-preview-help-example", "exec"), namespace)
    preview = namespace["preview"]
    assert isinstance(preview, PreviewResult)
    assert preview.rows == ({"value": 46.0},)
    assert preview.sample_policy.limit == 10000


def test_additivity_help_example_loads_a_sampled_status_axis_and_fold(
    semantic_project_factory: ProjectFactory,
    orders_backend: Backend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    example = _example("semantic.additive_all", capsys)
    source = (
        _IMPORTS
        + "inventory = ms.entity(name='inventory', datasource=ms.ref.datasource('warehouse'), source=md.table('inventory'))\n"
    )
    source += example
    project = _project(semantic_project_factory, source)
    assert project.is_ready(), project.errors()
    orders_backend.con.execute("CREATE TABLE inventory (sample_time TIMESTAMPTZ, quantity BIGINT)")
    orders_backend.con.execute("INSERT INTO inventory VALUES ('2026-01-01 00:00:00+00', 10)")
    materializer = Materializer(project, lambda _datasource: orders_backend)
    assert orders_backend.execute(materializer.measure("sales.inventory.quantity")).tolist() == [10]


@pytest.mark.parametrize(
    ("change", "replacement", "ready", "diagnostic"),
    (
        ("status_time_dimension=sample_time,", "", True, ""),
        (
            "sample_interval=(5, 'minute'), parse=ms.timestamp(timezone='UTC')",
            "parse=ms.timestamp(timezone='UTC')",
            True,
            "",
        ),
        (
            "status_time_dimension=sample_time,",
            "status_time_fold='last',",
            False,
            "retired declaration parameters",
        ),
        ("except_=(sample_time,)", "", False, "status time must be fixed"),
        (
            "status_time_dimension=sample_time,",
            "status_time_dimension=sample_time, status_time_fold=None,",
            False,
            "retired declaration parameters",
        ),
    ),
)
def test_additivity_help_rules_distinguish_fixed_coordinates_and_status_roles(
    semantic_project_factory: ProjectFactory,
    capsys: pytest.CaptureFixture[str],
    change: str,
    replacement: str,
    ready: bool,
    diagnostic: str,
) -> None:
    example = _example("semantic.additive_all", capsys).replace(change, replacement)
    if "sample_interval" not in example:
        example = example.replace(", status_time_fold='last'", "")
    source = (
        _IMPORTS
        + "inventory = ms.entity(name='inventory', datasource=ms.ref.datasource('warehouse'), source=md.table('inventory'))\n"
    )
    project = _project(semantic_project_factory, source + example)
    assert project.is_ready() is ready, project.errors()
    if diagnostic:
        assert any(diagnostic in str(error) for error in project.errors())


def test_sampled_metric_status_axis_requires_its_own_fold(
    semantic_project_factory: ProjectFactory,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = (
        _IMPORTS
        + "inventory = ms.entity(name='inventory', datasource=ms.ref.datasource('warehouse'), source=md.table('inventory'))\n"
    )
    source += _example("semantic.additive_all", capsys)
    source += (
        "@ms.metric(entities=[inventory], additivity=ms.additive_all(except_=(sample_time,)), "
        "status_time_dimension=sample_time)\n"
        "def latest(rows):\n    return ms.bind(quantity, rows).sum()\n"
    )
    project = _project(semantic_project_factory, source)
    error = next(
        error for error in project.errors() if error.kind == ErrorKind.INVALID_COMPONENT_BODY
    )
    assert "status_time_dimension" in str(error.received)
    assert error.expected and error.received and error.hint
    assert error.repair is not None
    assert "per(" in error.repair.action


@pytest.mark.parametrize(
    "target",
    (
        "entity",
        "metric",
        "bind",
        "additive_all",
        "Metric.where",
        "Entity.count",
        "row.sum",
        "preview",
    ),
)
def test_changed_help_is_equivalent_for_callables_and_has_only_resolvable_routes(
    target: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from marivo.semantic._capabilities.naming import semantic_target

    text = _help_text(f"semantic.{semantic_target(target)}", capsys)
    assert len(text) <= 7000
    assert len(text.splitlines()) <= 72
    callable_target: PublicHelpTarget = (
        ms.SemanticCatalog.preview
        if target == "preview"
        else "semantic.metric.where"
        if target == "Metric.where"
        else Entity.count
        if target == "Entity.count"
        else "semantic.row.sum"
        if target == "row.sum"
        else getattr(ms, target)
    )
    assert _help_text(callable_target, capsys) == text
    for route in re.findall(r'marivo\.help\("([A-Za-z0-9_.]+)"\)', text):
        assert _help_text(route, capsys)
    for consumers in re.findall(r"^  Consumers: (.+)$", text, flags=re.MULTILINE):
        for consumer in consumers.split(", "):
            assert _help_text(f"semantic.{consumer}", capsys)
    if target == "additive_all":
        assert "semi_additive" not in text
    if target == "preview":
        for scope in ("unpruned", "time_range", "partition"):
            assert f"datasource.{scope}" in text
        assert "semantic.preview_scope" not in text


def test_table_help_explains_string_projection_and_type_authority(
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = _help_text("datasource.table", capsys)
    assert _help_text(md.table, capsys) == text
    assert "Mapping[str, str]" in text and "source metadata" in text
    assert "not objects describing a column or its type" in text
    assert "md.source_column" not in text
    source = md.table("orders", columns={"amount": "total"})
    assert dict(source.columns or ()) == {"amount": "total"}


def test_authoring_help_examples_publish_a_cross_file_metric_from_empty_project(
    semantic_project_factory: ProjectFactory,
    tmp_path: Path,
    orders_backend: Backend,
    capsys: pytest.CaptureFixture[str],
) -> None:
    empty = ms.load(workspace_dir=tmp_path)
    assert not empty.search(kind="entity").items
    assert empty.get("datasource:default").kind == "datasource"
    semantic_project_factory(
        {
            "sales/_domain.py": _IMPORTS
            + "accountable_owner = 'Mina Zhang'\n"
            + _example("semantic.domain.define", capsys),
            "sales/model.py": _IMPORTS
            + _example("semantic.entity.define", capsys)
            + _example("semantic.measure.from_column", capsys),
            "sales/metrics.py": _IMPORTS
            + "amount = ms.ref.measure('sales.orders.amount')\n@ms.metric()\ndef revenue():\n    return amount.sum()\n",
        }
    )
    catalog = ms.load(workspace_dir=tmp_path)
    entry = catalog.get("metric:sales.revenue")
    assert entry.kind == "metric"
    assert catalog.search("sales.revenue", kind="metric").items[0].id == entry.id
    assert catalog.readiness(refs=[ms.ref.measure("sales.orders.amount")]).analysis_ready_inputs
    with patch_semantic_connections(catalog._project, lambda _datasource: orders_backend):
        assert catalog.preview(entry, scope=md.unpruned(max_rows=10, timeout_seconds=30)).rows == (
            {"value": 46},
        )
    # Question-scoped composition of closed values leaves the loaded directory fixed.
    doubled = entry.value * 2
    assert doubled is not entry.value
    assert [item.id for item in catalog.search(kind="metric").items] == ["metric:sales.revenue"]
