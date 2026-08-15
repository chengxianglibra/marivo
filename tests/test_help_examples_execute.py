"""Execute every datasource and semantic minimal example against real fixtures."""

from __future__ import annotations

import linecache
from pathlib import Path
from uuid import uuid4

import duckdb
import pytest

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo.datasource._capabilities.registry import REGISTRY as DATASOURCE_REGISTRY
from marivo.semantic._capabilities.registry import REGISTRY as SEMANTIC_REGISTRY
from marivo.semantic.loader import LoaderContext, LoaderContextManager

_DATASOURCE_EXAMPLES = tuple(
    (descriptor.canonical_id, descriptor.minimal_example)
    for descriptor in DATASOURCE_REGISTRY._descriptors
    if descriptor.minimal_example is not None
)
_SEMANTIC_EXAMPLES = tuple(
    (descriptor.canonical_id, descriptor.minimal_example)
    for descriptor in SEMANTIC_REGISTRY._descriptors
    if descriptor.minimal_example is not None
)
_SEMANTIC_RUNTIME_EXAMPLES = frozenset(
    {
        "load",
        "verify",
        "preview",
        "preview_many",
        "readiness",
        "richness",
        "parity_check",
        "SemanticCatalog.require",
        "SemanticCatalog.items",
    }
)


def _extend_orders_fixture(project_root: Path) -> None:
    """Add the columns referenced by the physical-evidence help examples."""
    connection = duckdb.connect(str(project_root / "warehouse.duckdb"))
    try:
        for name, sql_type, default in (
            ("order_id", "VARCHAR", "'order_1'"),
            ("customer_id", "VARCHAR", "'customer_1'"),
            ("id", "VARCHAR", "'order_1'"),
            ("status", "VARCHAR", "'accepted'"),
            ("event_date", "DATE", "DATE '2026-07-01'"),
        ):
            connection.execute(f"ALTER TABLE orders ADD COLUMN {name} {sql_type} DEFAULT {default}")
    finally:
        connection.close()


def _datasource_example_namespace(project_root: Path) -> dict[str, object]:
    _extend_orders_fixture(project_root)
    inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
    snapshot = inspection.sample(
        scope=md.unpruned(max_rows=1000, timeout_seconds=30),
        columns=(
            "query_id",
            "order_id",
            "amount",
            "customer_id",
            "id",
            "status",
            "event_date",
        ),
    )
    return {
        "md": md,
        "ms": ms,
        "inspection": inspection,
        "snapshot": snapshot,
        "other": snapshot,
    }


@pytest.mark.parametrize(
    ("canonical_id", "example"),
    _DATASOURCE_EXAMPLES,
    ids=(canonical_id for canonical_id, _ in _DATASOURCE_EXAMPLES),
)
def test_every_datasource_minimal_example_executes(
    authoring_evidence_project: Path,
    canonical_id: str,
    example: str,
) -> None:
    namespace = _datasource_example_namespace(authoring_evidence_project)
    if canonical_id == "register":
        md.remove("warehouse")
    exec(example, namespace)


def _semantic_authoring_namespace() -> dict[str, object]:
    orders = ms.ref.entity("sales.orders")
    customers = ms.ref.entity("sales.customers")
    created = ms.lifecycle_state(name="created", initial=True)
    paid = ms.lifecycle_state(name="paid", terminal=True)
    order_created = ms.ref.event("commerce.order_created")
    payment_captured = ms.ref.event("commerce.payment_captured")
    paid_transition = ms.transition(
        from_state=created,
        on=payment_captured,
        to_state=paid,
    )
    return {
        "md": md,
        "ms": ms,
        "accountable_owner": "Help Example Owner",
        "warehouse": md.duckdb("warehouse", path=":memory:"),
        "orders": orders,
        "customers": customers,
        "amount": ms.ref.measure("sales.orders.amount"),
        "region": ms.ref.dimension("sales.orders.region"),
        "state": ms.ref.dimension("sales.orders.state"),
        "snapshot_date": ms.ref.time_dimension("sales.orders.snapshot_date"),
        "valid_from": ms.ref.time_dimension("sales.orders.valid_from"),
        "valid_to": ms.ref.time_dimension("sales.orders.valid_to"),
        "log_date": ms.ref.time_dimension("sales.orders.log_date"),
        "order_customer_id": ms.ref.dimension("sales.orders.customer_id"),
        "customer_id": ms.ref.dimension("sales.customers.id"),
        "event_id": ms.ref.dimension("sales.orders.event_id"),
        "event_time": ms.ref.time_dimension("sales.orders.event_time"),
        "revenue": ms.ref.metric("sales.revenue"),
        "cost": ms.ref.metric("sales.cost"),
        "refund": ms.ref.metric("sales.refund"),
        "unit_price": ms.ref.measure("sales.orders.unit_price"),
        "volume": ms.ref.measure("sales.orders.volume"),
        "event_to_buyer": ms.ref.relationship("sales.event_to_buyer"),
        "payment_succeeded": ms.ref.event("sales.payment_succeeded"),
        "created": created,
        "paid": paid,
        "order_created": order_created,
        "paid_transition": paid_transition,
    }


def _semantic_runtime_namespace(
    project_root: Path,
    *,
    parity: bool,
) -> dict[str, object]:
    if parity:
        model_path = project_root / "models" / "semantic" / "sales" / "models.py"
        source = model_path.read_text(encoding="utf-8")
        metric_start = source.index("revenue = ms.aggregate(")
        model_path.write_text(
            source[:metric_start]
            + "@ms.metric(\n"
            + "    name='revenue', entities=[orders], additivity='additive', unit='USD',\n"
            + "    provenance=ms.from_sql(\n"
            + "        sql='SELECT SUM(amount) FROM orders', dialect='duckdb'\n"
            + "    ),\n"
            + ")\n"
            + "def revenue(orders):\n"
            + "    return ms.bind(amount, orders).sum()\n",
            encoding="utf-8",
        )
    catalog = ms.load()
    revenue = catalog.require(ms.ref.metric("sales.revenue"))
    inspection = md.inspect(ms.ref.datasource("warehouse"), md.table("orders"))
    orders_snapshot = inspection.sample(
        scope=md.unpruned(max_rows=1000, timeout_seconds=30),
        columns=("query_id", "region", "log_date", "log_hour", "amount"),
    )
    return {
        "md": md,
        "mv": mv,
        "ms": ms,
        "catalog": catalog,
        "revenue": revenue,
        "runtime_revenue": mv.runtime_metric.aggregate(
            ms.ref.measure("sales.orders.amount"),
            agg="sum",
            label="Runtime revenue",
        ),
        "orders_snapshot": orders_snapshot,
        "report": catalog.readiness(refs=[revenue]),
    }


def _exec_inspectable(example: str, namespace: dict[str, object]) -> None:
    """Execute an example through a synthetic source file inspect can resolve."""
    filename = f"<marivo-help-example-{uuid4().hex}>"
    source = example if example.endswith("\n") else f"{example}\n"
    linecache.cache[filename] = (len(source), None, source.splitlines(True), filename)
    try:
        exec(compile(source, filename, "exec"), namespace)
    finally:
        linecache.cache.pop(filename, None)


@pytest.mark.parametrize(
    ("canonical_id", "example"),
    _SEMANTIC_EXAMPLES,
    ids=(canonical_id for canonical_id, _ in _SEMANTIC_EXAMPLES),
)
def test_every_semantic_minimal_example_executes(
    authoring_evidence_project: Path,
    canonical_id: str,
    example: str,
) -> None:
    if canonical_id in _SEMANTIC_RUNTIME_EXAMPLES:
        namespace = _semantic_runtime_namespace(
            authoring_evidence_project,
            parity=canonical_id == "parity_check",
        )
        _exec_inspectable(example, namespace)
        return

    namespace = _semantic_authoring_namespace()
    context = LoaderContext(default_domain="sales")
    with LoaderContextManager(context):
        _exec_inspectable(example, namespace)


def test_representative_source_authored_examples_load_from_real_project_files(
    semantic_project_factory,
) -> None:
    """Source examples must work through the real loader, not only a hidden context."""

    examples = {
        canonical_id: example
        for canonical_id, example in _SEMANTIC_EXAMPLES
        if canonical_id
        in {
            "domain",
            "entity",
            "dimension_column",
            "time_dimension_column",
            "measure_column",
            "aggregate",
        }
    }
    domain_source = "\n".join(
        (
            "import marivo.semantic as ms",
            "accountable_owner = 'Help Example Owner'",
            examples["domain"],
            "",
        )
    )
    object_source = "\n".join(
        (
            "import marivo.datasource as md",
            "import marivo.semantic as ms",
            examples["entity"],
            examples["dimension_column"],
            examples["time_dimension_column"],
            examples["measure_column"],
            examples["aggregate"],
            "",
        )
    )
    project = semantic_project_factory(
        {
            "sales/_domain.py": domain_source,
            "sales/models.py": object_source,
        },
        load=False,
    )

    result = project.load()

    assert result.status == "ready", result.errors
    assert result.registry is not None
    assert "sales" in result.registry.domains
    assert "sales.orders" in result.registry.entities
    assert "sales.orders.region" in result.registry.dimensions
    assert "sales.orders.log_date" in result.registry.dimensions
    assert "sales.orders.amount" in result.registry.measures
    assert "sales.us_revenue" in result.registry.metrics
