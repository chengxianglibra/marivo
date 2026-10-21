"""Physical Entity and field materialization, source scopes, and resolver ownership.

Published Metric execution is covered through current public previews in
`test_metric_materialization.py` and the R2 Query/Artifact tests.
"""

from __future__ import annotations

import textwrap

import ibis
import pyarrow as pa
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource.source import PartitionScope
from marivo.semantic._deferred_semantics import consume_field, consume_row
from marivo.semantic.catalog import SemanticCatalog, SemanticKind
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.ir import EntityProvenance
from marivo.semantic.materializer import EntityRuntimeMetadata, Materializer
from tests.shared_fixtures import patch_semantic_connections
from tests.support.refs import make_ref

# ---------------------------------------------------------------------------
# DuckDB backend fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def duckdb_backend():
    """In-memory DuckDB backend with a test orders table."""
    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-02-01')"
    )
    return con


@pytest.fixture
def backend_factory(duckdb_backend):
    """A backend_factory callable that always returns the shared DuckDB backend."""

    def _factory(datasource_semantic_id: str):
        return duckdb_backend

    return _factory


# ---------------------------------------------------------------------------
# Fake DatasourceConnectionService for patching
# ---------------------------------------------------------------------------


def _materialize_dataset(project, connections, ref: str, *, source_bindings=None):
    return (
        SemanticCatalog(project)
        ._semantic_resolver(connections=connections, source_bindings=source_bindings)
        .entity(make_ref(ref, SemanticKind.ENTITY))
    )


def _materialize_field(project, connections, ref: str):
    return (
        SemanticCatalog(project)
        ._semantic_resolver(connections=connections)
        .dimension(make_ref(ref, SemanticKind.DIMENSION))
    )


# ---------------------------------------------------------------------------
# Model file templates
# ---------------------------------------------------------------------------


_DOMAIN_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    ms.domain(name="sales", owner='Mina Zhang', default=True)
""")

_DATASET_AND_METRIC_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))

    @ms.dimension(entity=orders)
    def amount(table):
        return table.amount

    amount_ref = ms.measure_column(name="amount_value", entity=orders, column="amount", additivity=ms.additive_all())
    finalized = ms.finalize()
    amount_value = finalized.resolve(amount_ref)
    @ms.metric()
    def total_amount():
        return amount_value.sum()
""")

_PROJECTED_DATASET_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    orders = ms.entity(
        name="orders",
        datasource=ms.ref.datasource("warehouse"),
        source=md.table(
            "orders",
            columns={
                "order_key": "order_id",
                "value": "amount",
            },
        ),
    )
""")

_COLUMN_HELPER_PROJECT_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
    amount = ms.measure_column(
        name="amount",
        entity=orders,
        column="amount",
        additivity=ms.additive_all(),
        unit="USD",
    )
    region = ms.dimension_column(name="region", entity=orders, column="region")
    created_at = ms.time_dimension_column(
        name="created_at",
        entity=orders,
        column="created_at",
        granularity="day",
        parse=ms.timestamp(timezone="UTC"),
        is_default=True,
    )
    finalized = ms.finalize()
    amount_value = finalized.resolve(amount)
    @ms.metric()
    def total_amount():
        return amount_value.sum()

    @ms.metric()
    def amount_again():
        return amount_value.sum()
""")

_SQL_VIEW_DATASET_PY = textwrap.dedent("""\
    import marivo.datasource as md
    import marivo.semantic as ms
    @ms.entity(name="orders_view", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
    def orders_view(backend):
        return backend.sql("SELECT * FROM orders")
""")


# ---------------------------------------------------------------------------
# Dataset materialize
# ---------------------------------------------------------------------------


def test_dataset_materialize(semantic_project_factory, backend_factory) -> None:
    """Materializing a dataset should return an ibis Table."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    assert project.is_ready()

    with patch_semantic_connections(project, backend_factory) as connections:
        table = _materialize_dataset(project, connections, "sales.orders")
    assert isinstance(table, ibis.Table)
    # The table should have the expected columns
    assert "amount" in table.columns
    assert "region" in table.columns
    rows = table.to_pandas()
    assert len(rows) == 2
    assert list(rows["amount"]) == [100.0, 200.0]


def test_dataset_materialize_applies_authoring_scope_at_physical_source(
    semantic_project_factory, backend_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    materializer = Materializer(
        project,
        backend_factory,
        entity_scopes={
            "sales.orders": PartitionScope(
                values=(("region", "EU"),),
                max_rows=1,
                timeout_seconds=30,
            )
        },
    )

    table = materializer.entity("sales.orders")

    assert table.to_pandas()["amount"].tolist() == [200.0]


def test_dataset_table_source_passes_database(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(\n"
                "    name='orders',\n"
                "    datasource=ms.ref.datasource('warehouse'),\n"
                "    source=md.table('orders', database='sales_mart'),\n"
                ")\n"
            ),
        }
    )

    class _Backend:
        def table(self, name, /, *, database=None):
            assert name == "orders"
            assert database == "sales_mart"
            return ibis.table({"amount": "float64"}, name=f"{database}.{name}")

    with patch_semantic_connections(project, lambda _: _Backend()) as connections:
        table = _materialize_dataset(project, connections, "sales.orders")
    assert table.get_name() == "sales_mart.orders"


def test_dataset_projected_table_materializes_output_aliases(
    semantic_project_factory,
    duckdb_backend,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _PROJECTED_DATASET_PY,
        }
    )

    with patch_semantic_connections(project, lambda _: duckdb_backend) as connections:
        table = _materialize_dataset(project, connections, "sales.orders")

    assert table.columns == ("order_key", "value")
    assert table.execute().to_dict(orient="records") == [
        {"order_key": 1, "value": 100.0},
        {"order_key": 2, "value": 200.0},
    ]
    compiled = str(duckdb_backend.compile(table))
    assert '"order_key"' in compiled
    assert '"value"' in compiled
    meta = project._runtime_metadata["sales.orders"]
    assert meta.entity_provenance == EntityProvenance.TABLE_PROJECTION
    assert meta.raw_sql_snippet is None


def test_sql_query_result_remains_sql_view_provenance(
    semantic_project_factory,
    duckdb_backend,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    materializer = Materializer(project, lambda _datasource_id: duckdb_backend)
    sql_view = duckdb_backend.sql("SELECT 1 AS value")

    materializer._detect_and_store_provenance(
        "sales.orders",
        md.table("orders"),
        sql_view,
    )

    meta = project._runtime_metadata["sales.orders"]
    assert meta.entity_provenance == EntityProvenance.SQL_VIEW
    assert meta.raw_sql_snippet == "SELECT 1 AS value"


def test_dataset_projected_table_uses_observed_lookup_schema(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _PROJECTED_DATASET_PY,
        }
    )

    class _LookupOnlyBackend:
        name = "duckdb"

        def table(self, name, /, *, database=None):
            return ibis.table({"order_id": "int64", "amount": "float32"}, name=name)

    with patch_semantic_connections(project, lambda _: _LookupOnlyBackend()) as connections:
        _materialize_dataset(project, connections, "sales.orders")


def test_dataset_file_source_reads_parquet(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(\n"
                "    name='orders',\n"
                "    datasource=ms.ref.datasource('warehouse'),\n"
                "    source=md.parquet('/data/orders/*.parquet', hive_partitioning=True),\n"
                ")\n"
            ),
        }
    )

    class _Backend:
        def read_parquet(self, path, **options):
            assert path == "/data/orders/*.parquet"
            assert options == {"hive_partitioning": True}
            return ibis.table({"order_id": "string", "amount": "int64"}, name="orders_file")

    with patch_semantic_connections(project, lambda _: _Backend()) as connections:
        table = _materialize_dataset(project, connections, "sales.orders")
    assert table.columns == ("order_id", "amount")
    assert table.schema() == ibis.schema({"order_id": "string", "amount": "int64"})


def test_dataset_csv_source_projects_inferred_columns(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(\n"
                "    name='orders',\n"
                "    datasource=ms.ref.datasource('warehouse'),\n"
                "    source=md.csv(\n"
                "        '/data/orders.csv',\n"
                "        columns={'order_id': 'order_id', 'amount': 'amount'},\n"
                "        header=False,\n"
                "        delimiter='|',\n"
                "    ),\n"
                ")\n"
            ),
        }
    )

    class _Backend:
        def read_csv(self, path, **options):
            assert path == "/data/orders.csv"
            assert options == {"header": False, "delimiter": "|"}
            return ibis.table({"order_id": "string", "amount": "int64"}, name="orders_file")

    with patch_semantic_connections(project, lambda _: _Backend()) as connections:
        table = _materialize_dataset(project, connections, "sales.orders")
    assert table.columns == ("amount", "order_id")
    assert table.schema() == ibis.schema({"amount": "int64", "order_id": "string"})


def test_dataset_json_source_projects_inferred_columns(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(\n"
                "    name='orders',\n"
                "    datasource=ms.ref.datasource('warehouse'),\n"
                "    source=md.json(\n"
                "        '/data/orders.json',\n"
                "        columns={'order_id': 'order_id', 'amount': 'amount'},\n"
                "        format='newline_delimited',\n"
                "    ),\n"
                ")\n"
            ),
        }
    )

    class _Backend:
        def read_json(self, path, **options):
            assert path == "/data/orders.json"
            assert options == {"format": "newline_delimited"}
            return ibis.table({"order_id": "string", "amount": "int64"}, name="orders_file")

    with patch_semantic_connections(project, lambda _: _Backend()) as connections:
        table = _materialize_dataset(project, connections, "sales.orders")
    assert table.columns == ("amount", "order_id")
    assert table.schema() == ibis.schema({"amount": "int64", "order_id": "string"})


def test_dataset_json_source_lowers_runtime_bindings_to_encoded_url(
    semantic_project_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(\n"
                "    name='orders',\n"
                "    datasource=ms.ref.datasource('warehouse'),\n"
                "    source=md.json(\n"
                "        'https://api.example/query?tenant=main',\n"
                "        columns={'value': 'value'},\n"
                "        query_params={\n"
                "            'query': 'sum(metric) by (cluster)',\n"
                "            'start': md.source_param('start'),\n"
                "        },\n"
                "    ),\n"
                ")\n"
            ),
        }
    )

    class _Backend:
        def raw_sql(self, query):
            assert "force_download" in query

        def create_table(self, name, *, obj, temp):
            assert name.startswith("_marivo_json_")
            assert temp is True
            assert isinstance(obj, pa.Table)
            return ibis.memtable(obj)

    from marivo.datasource import json_source

    requests: list[str] = []

    def request_payload(backend, source, supplied):
        requests.append(json_source.json_source_url(source, supplied))
        return [{"value": 1.25}]

    monkeypatch.setattr(json_source, "_request_payload", request_payload)

    with patch_semantic_connections(project, lambda _: _Backend()) as connections:
        table = _materialize_dataset(
            project,
            connections,
            "sales.orders",
            source_bindings={"sales.orders": {"start": "now-3600"}},
        )

    assert table.columns == ("value",)
    assert len(requests) == 1
    assert requests[0] == (
        "https://api.example/query?tenant=main&"
        "query=sum%28metric%29+by+%28cluster%29&start=now-3600"
    )


def test_dataset_file_source_requires_backend_reader(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(\n"
                "    name='orders',\n"
                "    datasource=ms.ref.datasource('warehouse'),\n"
                "    source=md.csv('/data/orders.csv'),\n"
                ")\n"
            ),
        }
    )

    with (
        patch_semantic_connections(project, lambda _: object()) as connections,
        pytest.raises(SemanticRuntimeError) as exc_info,
    ):
        _materialize_dataset(project, connections, "sales.orders")

    assert exc_info.value.kind == ErrorKind.MATERIALIZE_FAILED
    assert "does not support csv file sources" in exc_info.value.message


def test_dataset_json_source_requires_backend_reader(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "orders = ms.entity(\n"
                "    name='orders',\n"
                "    datasource=ms.ref.datasource('warehouse'),\n"
                "    source=md.json('/data/orders.json'),\n"
                ")\n"
            ),
        }
    )

    with (
        patch_semantic_connections(project, lambda _: object()) as connections,
        pytest.raises(SemanticRuntimeError) as exc_info,
    ):
        _materialize_dataset(project, connections, "sales.orders")

    assert exc_info.value.kind == ErrorKind.MATERIALIZE_FAILED
    assert "json file sources" in exc_info.value.message


# ---------------------------------------------------------------------------
# Field materialize
# ---------------------------------------------------------------------------


def test_field_materialize(semantic_project_factory, backend_factory) -> None:
    """Materializing a field should return an ibis Value expression."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    with patch_semantic_connections(project, backend_factory) as connections:
        field_expr = _materialize_field(project, connections, "sales.orders.amount")
    # ibis Value is the base of column expressions
    assert field_expr is not None


# ---------------------------------------------------------------------------
# Column helper parity
# ---------------------------------------------------------------------------


def test_column_helper_catalog_details_and_preview_requires_snapshot(
    semantic_project_factory,
    backend_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/columns.py": _COLUMN_HELPER_PROJECT_PY,
        }
    )
    catalog = SemanticCatalog(project)

    amount = catalog.fields.get("sales.orders.amount")
    assert consume_field(amount).reference.ref.kind == SemanticKind.MEASURE
    assert consume_field(amount).reference.ref.path == "sales.orders.amount"
    assert consume_row(amount).unit == "USD"
    assert catalog.describe(ms.ref.measure("sales.orders.amount")).unit == "USD"

    with (
        patch_semantic_connections(project, backend_factory),
        pytest.raises(TypeError) as connections,
    ):
        catalog.preview(amount, limit=2)  # type: ignore[call-arg]


def test_materializer_dimension_on_uses_supplied_table(
    semantic_project_factory, backend_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    mat = Materializer(project, backend_factory)
    supplied = ibis.table({"amount": "float64", "region": "string"}, name="supplied_orders")

    value = mat.dimension_on("sales.orders.amount", supplied)

    assert isinstance(value, ibis.expr.types.Value)
    assert "supplied_orders" in repr(value)


def test_materializer_dimension_on_is_not_ref_cached(
    semantic_project_factory, backend_factory
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    mat = Materializer(project, backend_factory)
    first = ibis.table({"amount": "float64", "region": "string"}, name="first_orders")
    second = ibis.table({"amount": "float64", "region": "string"}, name="second_orders")

    first_value = mat.dimension_on("sales.orders.amount", first)
    second_value = mat.dimension_on("sales.orders.amount", second)

    assert "first_orders" in repr(first_value)
    assert "second_orders" in repr(second_value)


# ---------------------------------------------------------------------------
# Backend cache
# ---------------------------------------------------------------------------


def test_backend_by_datasource_within_single_materializer(semantic_project_factory) -> None:
    """Within one Materializer, the same datasource should only call factory once."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    call_count = 0

    def counting_factory(datasource_id: str):
        nonlocal call_count
        call_count += 1
        con = ibis.duckdb.connect(":memory:")
        con.con.execute(
            "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
        )
        con.con.execute(
            "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), "
            "(2, 200.0, 'EU', '2025-02-01')"
        )
        return con

    # Use the Materializer directly to test internal caching
    mat = Materializer(project, counting_factory)
    mat.entity("sales.orders")
    assert call_count == 1

    # Calling again should use cache
    mat.entity("sales.orders")
    assert call_count == 1

    # Materialize a physical field on the same datasource; reuse its backend
    mat.dimension("sales.orders.amount")
    assert call_count == 1


# ---------------------------------------------------------------------------
# Dataset cache
# ---------------------------------------------------------------------------


def test_dataset_cache_reuses_table(semantic_project_factory) -> None:
    """Same dataset should return the same table object within a Materializer."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), (2, 200.0, 'EU', '2025-02-01')"
    )

    def factory(ds_id: str):
        return con

    mat = Materializer(project, factory)
    table1 = mat.entity("sales.orders")
    table2 = mat.entity("sales.orders")
    assert table1 is table2


# ---------------------------------------------------------------------------
# SQL escape hatch rejection
# ---------------------------------------------------------------------------


def test_dataset_decorator_body_rejected(semantic_project_factory) -> None:
    """Dataset bodies are no longer the physical-source entrypoint."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _SQL_VIEW_DATASET_PY,
        },
        load=False,
    )

    result = project.load()

    assert result.status == "errored"
    assert result.errors
    error = result.errors[0]
    assert error.kind == ErrorKind.ENTITY_CONSTRUCTOR_AS_DECORATOR
    assert error.constraint_id == "entity_constructor_assignment"
    assert error.location is not None and error.location.line == 3
    assert error.expected == "name = ms.entity(...) without a decorated function body"
    assert error.repair is not None
    assert error.repair.help_target.canonical_id == "entity.define"


@pytest.mark.parametrize(
    ("import_line", "decorator_name"),
    [
        ("import marivo.semantic as sem", "sem.entity"),
        ("from marivo.semantic import entity as declare_entity", "declare_entity"),
        ("from marivo import semantic as sem", "sem.entity"),
    ],
)
def test_entity_constructor_decorator_aliases_are_diagnosed(
    semantic_project_factory, import_line: str, decorator_name: str
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                f"import marivo.datasource as md\nimport marivo.semantic as ms\n{import_line}\n"
                f"@{decorator_name}(name='orders', datasource="
                "ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
                "def orders(backend):\n    return backend.table('orders')\n"
            ),
        },
        load=False,
    )
    result = project.load()
    assert result.status == "errored"
    assert result.errors[0].kind == ErrorKind.ENTITY_CONSTRUCTOR_AS_DECORATOR


def test_unrelated_ref_call_keeps_general_runtime_diagnostic(semantic_project_factory) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "entity_ref = ms.ref.entity('sales.orders')\n"
                "entity_ref()\n"
            ),
        },
        load=False,
    )
    result = project.load()
    assert result.status == "errored"
    assert result.errors[0].kind == ErrorKind.ORGANIZATION_ERROR


def test_shadowed_semantic_alias_keeps_general_runtime_diagnostic(
    semantic_project_factory,
) -> None:
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "ref = ms.ref.entity('sales.orders')\n"
                "class Other:\n"
                "    def entity(self, **kwargs):\n"
                "        return ref\n"
                "ms = Other()\n"
                "@ms.entity(name='orders')\n"
                "def orders():\n"
                "    pass\n"
            ),
        },
        load=False,
    )
    result = project.load()
    assert result.status == "errored"
    assert result.errors[0].kind == ErrorKind.ORGANIZATION_ERROR


def test_ibis_table_detection(semantic_project_factory, duckdb_backend) -> None:
    """Dataset using backend.table() should be detected as IBIS_TABLE provenance."""

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    with patch_semantic_connections(project, factory) as connections:
        _materialize_dataset(project, connections, "sales.orders")

    meta = project._runtime_metadata.get("sales.orders")
    assert meta is not None
    assert meta.entity_provenance == EntityProvenance.IBIS_TABLE
    assert meta.raw_sql_snippet is None


# ---------------------------------------------------------------------------
# Not found errors
# ---------------------------------------------------------------------------


def test_missing_semantic_refs_raise_not_found(semantic_project_factory, backend_factory) -> None:
    """Missing metric, entity, and dimension refs share the typed not-found outcome."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )
    with patch_semantic_connections(project, backend_factory) as connections:
        for operation in (
            lambda: SemanticCatalog(project).metrics.get("sales.nonexistent"),
            lambda: _materialize_dataset(project, connections, "sales.nonexistent"),
            lambda: _materialize_field(project, connections, "sales.orders.nonexistent"),
        ):
            with pytest.raises(SemanticRuntimeError) as exc_info:
                operation()
            assert exc_info.value.kind == ErrorKind.NOT_FOUND


# ---------------------------------------------------------------------------
# Fresh Materializer per call
# ---------------------------------------------------------------------------


def test_fresh_materializer_per_resolver_call(semantic_project_factory) -> None:
    """Each resolver helper call should create a new Materializer."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    factory_calls = []

    def tracking_factory(ds_id: str):
        factory_calls.append(ds_id)
        con = ibis.duckdb.connect(":memory:")
        con.con.execute(
            "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
        )
        con.con.execute(
            "INSERT INTO orders VALUES (1, 100.0, 'US', '2025-01-01'), "
            "(2, 200.0, 'EU', '2025-02-01')"
        )
        return con

    # First call creates a Materializer and calls factory
    with patch_semantic_connections(project, tracking_factory) as connections:
        _materialize_dataset(project, connections, "sales.orders")
    assert len(factory_calls) == 1

    # A separate physical-field resolver call creates another Materializer.
    with patch_semantic_connections(project, tracking_factory) as connections:
        _materialize_field(project, connections, "sales.orders.amount")
    assert len(factory_calls) == 2
    assert factory_calls == ["warehouse", "warehouse"]


# ---------------------------------------------------------------------------
# EntityRuntimeMetadata on project
# ---------------------------------------------------------------------------


def test_runtime_metadata_lifecycle(semantic_project_factory, duckdb_backend) -> None:
    """Materialization stores runtime metadata and reload clears the prior result."""

    def factory(ds_id: str):
        return duckdb_backend

    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_PY,
            "sales/datasets.py": _DATASET_AND_METRIC_PY,
        }
    )

    # Before materialization, no metadata
    assert project._runtime_metadata.get("sales.orders") is None

    # After materialization
    with patch_semantic_connections(project, factory) as connections:
        _materialize_dataset(project, connections, "sales.orders")
    meta = project._runtime_metadata.get("sales.orders")
    assert meta is not None
    assert isinstance(meta, EntityRuntimeMetadata)
    assert meta.entity_provenance == EntityProvenance.IBIS_TABLE
    assert meta.detected_at is not None

    project.load()
    assert project._runtime_metadata.get("sales.orders") is None


# ---------------------------------------------------------------------------
# Project must be loaded before materialize
# ---------------------------------------------------------------------------


def test_materialize_on_unloaded_project_raises(semantic_project_factory) -> None:
    """Calling materialize on an unloaded project should raise."""

    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )

    def factory(ds_id: str):
        return con

    project = semantic_project_factory(
        {"sales/_domain.py": _DOMAIN_PY, "sales/datasets.py": _DATASET_AND_METRIC_PY},
        load=False,
    )
    # Not loaded yet
    with (
        patch_semantic_connections(project, factory),
        pytest.raises(ms.errors.SemanticLoadFailed) as connections,
    ):
        _materialize_dataset(project, connections, "sales.orders")


def test_materialization_sample_size_bounds_entity_output(semantic_project_factory) -> None:
    """The retained physical materializer bounds Entity output rows."""
    con = ibis.duckdb.connect(":memory:")
    con.con.execute(
        "CREATE TABLE orders (order_id INT, amount FLOAT, region TEXT, created_at TIMESTAMP)"
    )
    con.con.execute(
        "INSERT INTO orders SELECT i, CAST(i AS DOUBLE), 'US', TIMESTAMP '2025-01-01' "
        "FROM range(1, 12) AS rows(i)"
    )

    def factory(ds_id: str):
        return con

    project = semantic_project_factory(
        {"sales/_domain.py": _DOMAIN_PY, "sales/datasets.py": _DATASET_AND_METRIC_PY}
    )

    mat_full = Materializer(project, factory)
    table_full = mat_full.entity("sales.orders")
    assert len(table_full.to_pandas()) == 11

    mat_sampled = Materializer(project, factory, sample_size=5)
    table_sampled = mat_sampled.entity("sales.orders")
    assert len(table_sampled.to_pandas()) == 5
