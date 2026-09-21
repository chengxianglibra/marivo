"""Entity-expression materialization through the shared resolver boundary.

Covers Phase 2 of the entity-expression design: the materializer resolves the
declared Source, applies explicit physical input scopes before the Entity
body, evaluates the body once, requires a single-Source-derived Ibis Table,
and applies output row budgets after the body. Direct declarations keep the
identity relation and their existing behavior.
"""

from __future__ import annotations

import ibis
import pytest

import marivo.datasource as md
import marivo.semantic as ms
from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import ErrorKind, SemanticRuntimeError
from marivo.semantic.ir import EntityProvenance
from marivo.semantic.materializer import Materializer
from tests.test_semantic_materializer import _patch_connection_service

_WAREHOUSE_FILES = {
    "datasources/warehouse.py": (
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    ),
    "sales/_domain.py": (
        "import marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang', default=True)\n"
    ),
}

_ORDERS_DDL = (
    "CREATE TABLE orders ("
    "order_id INT, amount DOUBLE, region TEXT, is_deleted BOOLEAN, "
    "updated_at TIMESTAMP, revision_id INT)"
)
_ORDERS_ROWS = (
    "INSERT INTO orders VALUES "
    "(1, 10.0, 'east', FALSE, TIMESTAMP '2026-01-01 00:00:00', 1), "
    "(1, 12.0, 'east', FALSE, TIMESTAMP '2026-01-02 00:00:00', 2), "
    "(2, 20.0, 'west', TRUE, TIMESTAMP '2026-01-01 00:00:00', 1), "
    "(2, 25.0, 'west', FALSE, TIMESTAMP '2026-01-02 00:00:00', 3), "
    "(3, 30.0, 'east', FALSE, TIMESTAMP '2026-01-03 00:00:00', 1)"
)


def _warehouse_connection() -> ibis.backends.duckdb.Backend:
    connection = ibis.duckdb.connect(":memory:")
    connection.con.execute(_ORDERS_DDL)
    connection.con.execute(_ORDERS_ROWS)
    return connection


_DEDUP_MODEL = """\
import ibis
from ibis import _

import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("warehouse")


@ms.entity(datasource=wh, source=md.table("orders"))
def latest_orders(raw):
    \"\"\"One latest, non-deleted order per order ID.\"\"\"
    return raw.filter(
        ibis.row_number().over(
            ibis.window(
                group_by=[raw["order_id"]],
                order_by=[raw["updated_at"].desc(), raw["revision_id"].desc()],
            )
        )
        == 0
    ).filter(_["is_deleted"] == False)


amount = ms.measure_column(
    name="amount", entity=latest_orders, column="amount", additivity="additive", unit="USD"
)
order_id = ms.dimension_column(name="order_id", entity=latest_orders, column="order_id")
revenue = ms.aggregate(name="revenue", measure=amount, agg="sum")
"""

_GROUPED_MODEL = """\
import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("warehouse")


@ms.entity(datasource=wh, source=md.table("orders"))
def regional_sales(raw):
    \"\"\"Order amounts and row counts per region.\"\"\"
    return raw.group_by("region").aggregate(
        sales_amount=raw["amount"].sum(),
        order_count=raw.count(),
    )


region = ms.dimension_column(name="region", entity=regional_sales, column="region")
sales_amount = ms.measure_column(
    name="sales_amount", entity=regional_sales, column="sales_amount",
    additivity="additive", unit="USD",
)
order_count = ms.measure_column(
    name="order_count", entity=regional_sales, column="order_count",
    additivity="additive", unit="USD",
)
regional_revenue = ms.aggregate(name="regional_revenue", measure=sales_amount, agg="sum")
"""

_DIRECT_MODEL = """\
import marivo.datasource as md
import marivo.semantic as ms

orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"))
"""


def _expr_project(semantic_project_factory, model: str):
    project = semantic_project_factory({**_WAREHOUSE_FILES, "sales/entities.py": model})
    assert project.is_ready(), project.errors()
    return project


# ---------------------------------------------------------------------------
# Output relation, grain, and shared-resolver execution
# ---------------------------------------------------------------------------


def test_expression_entity_materializes_output_rows_and_metric(
    semantic_project_factory,
) -> None:
    project = _expr_project(semantic_project_factory, _DEDUP_MODEL)
    connection = _warehouse_connection()
    materializer = Materializer(project, lambda _datasource: connection)

    table = materializer.entity("sales.latest_orders")
    rows = connection.execute(table)
    assert sorted(rows["order_id"].tolist()) == [1, 2, 3]
    assert rows.set_index("order_id")["amount"].to_dict() == {1: 12.0, 2: 25.0, 3: 30.0}

    assert connection.execute(materializer.metric("sales.revenue")) == 67.0


def test_expression_entity_grouping_exposes_output_grain(
    semantic_project_factory,
) -> None:
    project = _expr_project(semantic_project_factory, _GROUPED_MODEL)
    connection = _warehouse_connection()
    materializer = Materializer(project, lambda _datasource: connection)

    table = materializer.entity("sales.regional_sales")
    assert list(table.columns) == ["region", "sales_amount", "order_count"]
    grouped = connection.execute(table).set_index("region")
    assert grouped["sales_amount"].to_dict() == {"east": 52.0, "west": 45.0}
    assert grouped["order_count"].to_dict() == {"east": 3, "west": 2}
    assert connection.execute(materializer.metric("sales.regional_revenue")) == 97.0
    assert connection.execute(materializer.dimension("sales.regional_sales.region")).tolist() == [
        "east",
        "west",
    ]


def test_expression_entity_accepts_self_derived_branches(
    semantic_project_factory,
) -> None:
    branch_model = """\
import ibis

import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("warehouse")


@ms.entity(datasource=wh, source=md.table("orders"))
def branched(raw):
    \"\"\"Union of two branches derived from the one injected Source.\"\"\"
    return ibis.union(raw.filter(raw["amount"] > 15), raw.filter(raw["amount"] > 25))


branch_amount = ms.measure_column(
    name="branch_amount", entity=branched, column="amount", additivity="additive", unit="USD"
)
"""
    project = _expr_project(semantic_project_factory, branch_model)
    connection = _warehouse_connection()

    rows = connection.execute(
        Materializer(project, lambda _ds: connection).entity("sales.branched")
    )
    assert sorted(rows["amount"].tolist()) == [20.0, 25.0, 30.0, 30.0]


# ---------------------------------------------------------------------------
# Budget placement: physical scope before the body, row limits after it
# ---------------------------------------------------------------------------


def test_partition_scope_applies_before_entity_body(semantic_project_factory) -> None:
    project = _expr_project(semantic_project_factory, _DEDUP_MODEL)
    connection = _warehouse_connection()
    scope = md.partition({"region": "east"}, max_rows=100, timeout_seconds=30)
    materializer = Materializer(
        project,
        lambda _datasource: connection,
        entity_scopes={"sales.latest_orders": scope},
    )

    rows = connection.execute(materializer.entity("sales.latest_orders"))
    # East scoped raw rows dedupe to one latest winner per order ID: the scope
    # predicate ran before the body, so order 2 (west) never enters it.
    assert rows.set_index("order_id")["amount"].to_dict() == {1: 12.0, 3: 30.0}


def test_scope_max_rows_limits_entity_output_not_raw_input(
    semantic_project_factory,
) -> None:
    project = _expr_project(semantic_project_factory, _DEDUP_MODEL)
    connection = _warehouse_connection()
    scope = md.unpruned(max_rows=1, timeout_seconds=30)
    materializer = Materializer(
        project,
        lambda _datasource: connection,
        entity_scopes={"sales.latest_orders": scope},
    )

    rows = connection.execute(materializer.entity("sales.latest_orders"))
    # Five raw rows feed the dedup; the max_rows budget bounds Entity output
    # only. A pre-body truncation would have kept (1, 10.0) instead of a
    # deduplicated latest winner.
    winners = set(zip(rows["order_id"].tolist(), rows["amount"].tolist(), strict=True))
    assert len(winners) == 2
    assert winners <= {(1, 12.0), (2, 25.0), (3, 30.0)}
    assert (1, 10.0) not in winners and (2, 20.0) not in winners


def test_metric_sample_size_limits_entity_output_before_aggregation(
    semantic_project_factory,
) -> None:
    project = _expr_project(semantic_project_factory, _GROUPED_MODEL)
    connection = _warehouse_connection()
    materializer = Materializer(project, lambda _datasource: connection, sample_size=1)

    rows = connection.execute(materializer.entity("sales.regional_sales"))
    # The sample budget keeps one Entity output row, which must be a complete
    # group aggregate. A pre-body raw limit would have aggregated only
    # order 1, producing sales_amount=10.0 and order_count=1.
    assert len(rows) == 1
    assert rows["sales_amount"].tolist() in ([52.0], [45.0])
    assert rows["order_count"].tolist() in ([3], [2])


# ---------------------------------------------------------------------------
# Single-Source boundary enforcement
# ---------------------------------------------------------------------------


def test_expression_entity_rejects_captured_memtable_root(
    semantic_project_factory,
) -> None:
    model = _DIRECT_MODEL + (
        "\n"
        "import ibis\n"
        "\n"
        "@ms.entity(datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "def seeded(raw):\n"
        "    return ibis.memtable({'order_id': [1, 2, 3]})\n"
    )
    project = _expr_project(semantic_project_factory, model)
    connection = _warehouse_connection()

    with pytest.raises(SemanticRuntimeError) as exc_info:
        Materializer(project, lambda _ds: connection).entity("sales.seeded")
    assert exc_info.value.kind == ErrorKind.SQL_ESCAPE_HATCH
    assert "sales.seeded" in str(exc_info.value)


def test_expression_entity_rejects_scope_bypass_through_op_ancestors(
    semantic_project_factory,
) -> None:
    """A body must not walk above the injected relation to escape input scope.

    ``raw.op().parent.to_expr()`` returns the unscoped physical table beneath
    the injected boundary, so a public preview scoped to region='east' would
    silently return west rows.
    """
    model = _DIRECT_MODEL + (
        "\n"
        "@ms.entity(datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "def unscoped(raw):\n"
        "    return raw.op().parent.to_expr()\n"
    )
    project = _expr_project(semantic_project_factory, model)
    connection = _warehouse_connection()
    scope = md.partition({"region": "east"}, max_rows=100, timeout_seconds=30)
    materializer = Materializer(
        project,
        lambda _ds: connection,
        entity_scopes={"sales.unscoped": scope},
    )

    with pytest.raises(SemanticRuntimeError) as exc_info:
        materializer.entity("sales.unscoped")
    assert exc_info.value.kind == ErrorKind.SQL_ESCAPE_HATCH
    assert "sales.unscoped" in str(exc_info.value)

    # The same rejection guards the public preview path.
    catalog = SemanticCatalog(project)
    with (
        _patch_connection_service(project, lambda _name: connection),
        pytest.raises(SemanticRuntimeError) as preview_info,
    ):
        catalog.preview(
            ms.ref.entity("sales.unscoped"),
            scope=scope,
        )
    assert preview_info.value.kind == ErrorKind.SQL_ESCAPE_HATCH


def test_expression_entity_without_scope_allows_op_ancestors(
    semantic_project_factory,
) -> None:
    """Without an input scope the op chain is the Source itself; no bypass.

    The same expression is valid when no explicit scope exists: the injected
    boundary is the physical Source, and reading its ancestors cannot escape
    any requested filter.
    """
    model = _DIRECT_MODEL + (
        "\n"
        "@ms.entity(datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "def rescope(raw):\n"
        "    return raw.op().to_expr()\n"
    )
    project = _expr_project(semantic_project_factory, model)
    connection = _warehouse_connection()

    rows = Materializer(project, lambda _ds: connection).entity("sales.rescope")
    assert len(connection.execute(rows)) == 5


def test_expression_entity_rejects_detached_same_named_table(
    semantic_project_factory,
) -> None:
    model = _DIRECT_MODEL + (
        "\n"
        "import ibis\n"
        "\n"
        "@ms.entity(datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "def shadow(raw):\n"
        "    return ibis.table({'order_id': 'int64', 'amount': 'float64'}, name='orders')\n"
    )
    project = _expr_project(semantic_project_factory, model)
    connection = _warehouse_connection()

    with pytest.raises(SemanticRuntimeError) as exc_info:
        Materializer(project, lambda _ds: connection).entity("sales.shadow")
    assert exc_info.value.kind == ErrorKind.SQL_ESCAPE_HATCH


def test_expression_entity_missing_output_primary_key_fails(
    semantic_project_factory,
) -> None:
    """A declared primary key must exist on the Entity output schema."""
    model = _DIRECT_MODEL + (
        "\n"
        "@ms.entity(\n"
        "    datasource=ms.ref.datasource('warehouse'),\n"
        "    source=md.table('orders'),\n"
        "    primary_key=['order_id'],\n"
        ")\n"
        "def regions_only(raw):\n"
        "    return raw.select('region').distinct()\n"
    )
    project = _expr_project(semantic_project_factory, model)
    connection = _warehouse_connection()

    with pytest.raises(SemanticRuntimeError) as exc_info:
        Materializer(project, lambda _ds: connection).entity("sales.regions_only")
    assert exc_info.value.kind == ErrorKind.BINDING_RESULT_INVALID
    message = str(exc_info.value)
    assert "order_id" in message
    assert "region" in message


def test_expression_entity_output_primary_key_satisfied_passes(
    semantic_project_factory,
) -> None:
    """A dedup key present in the output relation keeps materialization green."""
    model = """\
import ibis
from ibis import _

import marivo.datasource as md
import marivo.semantic as ms

wh = ms.ref.datasource("warehouse")


@ms.entity(datasource=wh, source=md.table("orders"), primary_key=["order_id"])
def keyed_latest(raw):
    \"\"\"One latest non-deleted order per order ID.\"\"\"
    return raw.filter(
        ibis.row_number().over(
            ibis.window(
                group_by=[raw["order_id"]],
                order_by=[raw["updated_at"].desc(), raw["revision_id"].desc()],
            )
        )
        == 0
    ).filter(_["is_deleted"] == False)


keyed_amount = ms.measure_column(
    name="keyed_amount", entity=keyed_latest, column="amount", additivity="additive", unit="USD"
)
"""
    project = _expr_project(semantic_project_factory, model)
    connection = _warehouse_connection()

    table = Materializer(project, lambda _ds: connection).entity("sales.keyed_latest")
    assert "order_id" in table.columns


def test_expression_entity_scalar_return_fails_at_materialization(
    semantic_project_factory,
) -> None:
    model = _DIRECT_MODEL + (
        "\n"
        "@ms.entity(datasource=ms.ref.datasource('warehouse'), source=md.table('orders'))\n"
        "def scalar_total(raw):\n"
        "    return raw.amount.sum()\n"
    )
    project = semantic_project_factory({**_WAREHOUSE_FILES, "sales/entities.py": model})
    assert project.is_ready(), project.errors()
    connection = _warehouse_connection()

    with pytest.raises(SemanticRuntimeError) as exc_info:
        Materializer(project, lambda _ds: connection).entity("sales.scalar_total")
    assert exc_info.value.kind == ErrorKind.BINDING_RESULT_INVALID


# ---------------------------------------------------------------------------
# Direct declarations stay on the identity relation
# ---------------------------------------------------------------------------


def test_direct_entity_materialization_unchanged(semantic_project_factory) -> None:
    from marivo.refs import ref as ref_factory

    project = _expr_project(semantic_project_factory, _DIRECT_MODEL)
    connection = _warehouse_connection()

    sidecar = project._expression_sidecar
    assert sidecar is not None
    assert ref_factory.entity("sales.orders") not in sidecar.bodies

    table = Materializer(project, lambda _ds: connection).entity("sales.orders")
    assert list(table.columns) == [
        "order_id",
        "amount",
        "region",
        "is_deleted",
        "updated_at",
        "revision_id",
    ]
    assert len(connection.execute(table)) == 5


def test_expression_entity_provenance_reports_physical_source(
    semantic_project_factory,
) -> None:
    project = _expr_project(semantic_project_factory, _DEDUP_MODEL)
    connection = _warehouse_connection()
    Materializer(project, lambda _ds: connection).entity("sales.latest_orders")

    meta = project._runtime_metadata["sales.latest_orders"]
    # The window/dedup transformation must not reclassify the Entity as a SQL
    # view; provenance keeps describing the physical declared Source.
    assert meta.entity_provenance is EntityProvenance.IBIS_TABLE
    assert meta.raw_sql_snippet is None


# ---------------------------------------------------------------------------
# Preview disclosure over Entity output
# ---------------------------------------------------------------------------


def test_entity_preview_reports_output_columns(semantic_project_factory) -> None:
    import marivo.semantic as ms

    project = _expr_project(semantic_project_factory, _GROUPED_MODEL)
    connection = _warehouse_connection()
    catalog = SemanticCatalog(project)

    with _patch_connection_service(project, lambda _name: connection):
        result = catalog.preview(
            ms.ref.entity("sales.regional_sales"),
            scope=md.unpruned(max_rows=100, timeout_seconds=30),
        )

    assert result.status == "passed"
    assert tuple(result.columns) == ("region", "sales_amount", "order_count")
    assert result.sample_policy.method == "bounded_limit"
    assert {row["region"] for row in result.rows} == {"east", "west"}


def test_metric_preview_over_expression_entity_keeps_sampling_disclosure(
    semantic_project_factory,
) -> None:
    import marivo.semantic as ms

    project = _expr_project(semantic_project_factory, _DEDUP_MODEL)
    connection = _warehouse_connection()
    catalog = SemanticCatalog(project)

    with _patch_connection_service(project, lambda _name: connection):
        result = catalog.preview(
            ms.ref.metric("sales.revenue"),
            scope=md.unpruned(max_rows=100, timeout_seconds=30),
        )

    assert result.rows == ({"value": 67.0},)
    assert result.sample_policy.method == "pre_aggregate_limit"
    warning_kinds = {warning.kind for warning in result.warnings}
    assert "approximate_preview" in warning_kinds
