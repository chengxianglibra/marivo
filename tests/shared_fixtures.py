"""Shared lightweight fixtures for Python-native analysis tests."""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager, suppress
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb
import ibis

if TYPE_CHECKING:
    from marivo.semantic.catalog import SemanticCatalog

# ---------------------------------------------------------------------------
# Named DuckDB templates (versioned, cached in /tmp)
# ---------------------------------------------------------------------------
# Bump the version string when seeded schema or rows change so cached
# copies rebuild automatically.

_SALES_ORDERS_V = "v1"
_AUTHORING_EVIDENCE_V = "v2"


def rendered_help(target: object | None = None, *, owner: str | None = None) -> str:
    """Return private unified-help text for behavioral assertions.

    ``owner`` qualifies native string targets and selects a native root page.
    Public API tests should call ``marivo.help`` and capture stdout instead.
    """
    if owner is not None and target is None:
        if owner == "datasource":
            from marivo.datasource._capabilities.render import render_root_help
        elif owner == "semantic":
            from marivo.semantic._capabilities.render import render_root_help
        elif owner == "analysis":
            from marivo.analysis._capabilities.render import render_root_help
        else:
            raise ValueError(f"unknown help owner: {owner!r}")
        return render_root_help()
    if owner is not None and isinstance(target, str):
        target = f"{owner}.{target}"
    from marivo._help.render import render_help_text

    return render_help_text(target)[0]


def fiscal_analysis_project_files() -> dict[str, str]:
    """Return the compact fiscal-calendar project used by temporal tests."""

    return {
        "sales/_domain.py": (
            "import marivo.semantic as ms\nms.domain(name='sales', owner='Data', default=True)\n"
        ),
        "sales/calendar.py": (
            "import marivo.datasource as md\n"
            "import marivo.semantic as ms\n"
            "calendar = ms.entity(name='calendar', datasource=ms.ref.datasource('warehouse'), source=md.table('calendar'))\n"
            "calendar_date = ms.time_dimension_column(name='calendar_date', entity=calendar, column='calendar_date', granularity='day')\n"
            "fiscal_week = ms.dimension_column(name='fiscal_week', entity=calendar, column='fiscal_week')\n"
            "fiscal_month = ms.dimension_column(name='fiscal_month', entity=calendar, column='fiscal_month')\n"
            "fiscal = ms.period_calendar(name='fiscal', date=calendar_date, boundary_timezone='UTC', coverage=(__import__('datetime').date(2026, 1, 1), __import__('datetime').date(2026, 3, 1)), levels={'fiscal_week': fiscal_week, 'fiscal_month': fiscal_month})\n"
        ),
        "sales/metrics.py": (
            "import marivo.datasource as md\n"
            "import marivo.semantic as ms\n"
            "events = ms.entity(name='events', datasource=ms.ref.datasource('warehouse'), source=md.table('events'))\n"
            "event_date = ms.time_dimension_column(name='event_date', entity=events, column='event_date', granularity='day')\n"
            "amount = ms.measure_column(name='amount', entity=events, column='amount', additivity='additive', unit='USD')\n"
            "user_id = ms.measure_column(name='user_id', entity=events, column='user_id', additivity='non_additive')\n"
            "gmv = ms.aggregate(name='gmv', measure=amount, agg='sum')\n"
            "active_users = ms.aggregate(name='active_users', measure=user_id, agg='count_distinct')\n"
            "weighted_user = ms.weighted_mean(name='weighted_user', value=user_id, weight=amount)\n"
            "fiscal_mtd = ms.cumulative(name='fiscal_mtd', base=gmv, over=event_date, anchor=ms.grain_to_date(grain=ms.calendar_grain(calendar=ms.ref.period_calendar('sales.fiscal'), level='fiscal_month')))\n"
            "fiscal_active_users = ms.cumulative(name='fiscal_active_users', base=active_users, over=event_date, anchor=ms.grain_to_date(grain=ms.calendar_grain(calendar=ms.ref.period_calendar('sales.fiscal'), level='fiscal_month')))\n"
            "fiscal_weighted_user = ms.cumulative(name='fiscal_weighted_user', base=weighted_user, over=event_date, anchor=ms.grain_to_date(grain=ms.calendar_grain(calendar=ms.ref.period_calendar('sales.fiscal'), level='fiscal_month')))\n"
        ),
    }


def publish_fiscal_calendar_artifact(catalog: SemanticCatalog) -> None:
    """Publish the certified fiscal calendar used by analysis-only tests."""

    import marivo.semantic as ms
    from marivo._temporal import (
        TemporalSnapshotStore,
        certify_period_calendar_rows,
        period_calendar_definition_digest,
    )
    from marivo.semantic._definition_identity import scoped_definition_fingerprint

    rows: list[dict[str, str]] = []
    cursor = date(2026, 1, 1)
    while cursor < date(2026, 3, 1):
        month = "M1" if cursor.month == 1 else "M2"
        week = f"{month}-W{((cursor.day - 1) // 7) + 1}"
        rows.append(
            {
                "calendar_date": cursor.isoformat(),
                "fiscal_week": week,
                "fiscal_month": month,
            }
        )
        cursor += timedelta(days=1)
    calendar_ref = ms.ref.period_calendar("sales.fiscal")
    calendar = catalog._require_ready().period_calendars[calendar_ref.path]
    snapshot = certify_period_calendar_rows(
        calendar_ref=calendar_ref,
        boundary_timezone=calendar.boundary_timezone,
        coverage=(
            date.fromisoformat(calendar.coverage[0]),
            date.fromisoformat(calendar.coverage[1]),
        ),
        columns=("calendar_date", "fiscal_week", "fiscal_month"),
        retained_values=tuple(rows),
        date_column="calendar_date",
        levels={
            "fiscal_week": "fiscal_week",
            "fiscal_month": "fiscal_month",
        },
    )
    dependency_digest = scoped_definition_fingerprint(
        root=calendar_ref,
        definitions=catalog._state.definitions,
        dependencies=catalog._state.dependencies,
        sidecar=catalog._state.sidecar,
    )
    TemporalSnapshotStore(catalog.workspace_dir).publish(
        snapshot,
        definition_digest=period_calendar_definition_digest(
            calendar_ref=calendar_ref,
            boundary_timezone=calendar.boundary_timezone,
            coverage=calendar.coverage,
            levels=calendar.levels,
            correspondences=calendar.correspondences,
            dependency_digest=dependency_digest,
        ),
    )


def _template_cache_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "marivo_test_templates"
    d.mkdir(exist_ok=True)
    return d


def sales_orders_template() -> Path:
    """Cached DuckDB file with the standard orders table.

    Schema: orders(order_id INTEGER, created_at DATE, amount DOUBLE,
                   region VARCHAR, user_id INTEGER)

    Rows: 4 rows covering 2026-07/08/09 with north/south regions.
    """
    cache = _template_cache_dir() / f"sales_orders_{_SALES_ORDERS_V}.duckdb"
    if cache.exists():
        return cache

    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=cache.parent,
        prefix=f"{cache.name}.",
        suffix=".building",
    ) as tmp_file:
        tmp = Path(tmp_file.name)
    try:
        # DuckDB 1.5+ refuses to open an existing 0-byte file, so remove the
        # placeholder NamedTemporaryFile (used only to reserve a unique name)
        # before connecting; duckdb.connect then creates a fresh database.
        tmp.unlink()
        con = duckdb.connect(str(tmp))
        try:
            con.execute(
                "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
                "amount DOUBLE, region VARCHAR, user_id INTEGER)"
            )
            con.execute(
                "INSERT INTO orders VALUES "
                "(1, DATE '2026-07-01', 10.0, 'north', 100),"
                "(2, DATE '2026-07-02', 20.0, 'north', 100),"
                "(3, DATE '2026-08-01', 30.0, 'south', 200),"
                "(4, DATE '2026-09-15', 40.0, 'north', 300)"
            )
        finally:
            con.close()

        os.replace(tmp, cache)
    finally:
        with suppress(FileNotFoundError):
            tmp.unlink()
    return cache


def authoring_evidence_template() -> Path:
    """Return a cached DuckDB fixture for the complete authoring workflow."""
    cache = _template_cache_dir() / f"authoring_evidence_{_AUTHORING_EVIDENCE_V}.duckdb"
    if cache.exists():
        return cache

    with tempfile.NamedTemporaryFile(
        delete=False,
        dir=cache.parent,
        prefix=f"{cache.name}.",
        suffix=".building",
    ) as tmp_file:
        tmp = Path(tmp_file.name)
    try:
        tmp.unlink()
        con = duckdb.connect(str(tmp))
        try:
            con.execute(
                "CREATE TABLE orders ("
                "query_id INTEGER, self VARCHAR, region VARCHAR, log_date VARCHAR, "
                "log_hour INTEGER, amount DOUBLE, uncommon_date VARCHAR, epoch_like BIGINT)"
            )
            con.execute(
                "INSERT INTO orders VALUES "
                "(1, 'https://private.example/orders/1', 'moon-base', '20410717', "
                "0, 125.25, '17-Jul-2041', 2257632000),"
                "(2, 'https://private.example/orders/2', 'orbital', '20410717', "
                "12, 250.50, '18-Jul-2041', 2257718400),"
                "(3, 'https://private.example/orders/3', 'moon-base', '20410718', "
                "23, 375.75, '19-Jul-2041', 2257804800),"
                "(4, 'https://private.example/orders/4', 'orbital', '20410230', "
                "24, 0.0, '20-Jul-2041', 2257891200)"
            )
            con.execute("CREATE TABLE orders_replica AS SELECT * FROM orders")
        finally:
            con.close()
        os.replace(tmp, cache)
    finally:
        with suppress(FileNotFoundError):
            tmp.unlink()
    return cache


def connect_sales_orders() -> ibis.duckdb.DuckDBBackend:
    """Create an in-memory DuckDB seeded from the sales_orders template.

    Uses ATTACH READ_ONLY to bulk-copy the orders table from the cached
    template file.  READ_ONLY avoids lock conflicts when xdist workers
    share the same template file.
    """
    template = sales_orders_template()
    con = ibis.duckdb.connect(":memory:")
    con.raw_sql(f"ATTACH '{template}' AS _tpl (READ_ONLY)")
    con.raw_sql("CREATE TABLE orders AS SELECT * FROM _tpl.orders")
    con.raw_sql("DETACH _tpl")
    return con


def sales_backends(con: ibis.duckdb.DuckDBBackend) -> dict:
    """Standard backends dict wrapping a DuckDB connection as 'warehouse'."""
    return {"warehouse": lambda: con}


# ---------------------------------------------------------------------------
# Project directory templates (versioned, cached in /tmp)
# ---------------------------------------------------------------------------

_SALES_PROJECT_V = "v1"


def sales_project_template(*, with_time: bool = True) -> Path:
    """Cached directory tree with models/semantic/sales/ project files.

    Bump _SALES_PROJECT_V when the project files change.
    """
    tag = "with_time" if with_time else "no_time"
    cache = _template_cache_dir() / f"sales_project_{_SALES_PROJECT_V}" / tag
    if cache.exists():
        return cache

    cache.parent.mkdir(parents=True, exist_ok=True)
    building = Path(tempfile.mkdtemp(dir=cache.parent, prefix=f"{tag}.building."))
    (building / "marivo.toml").write_text('[project]\nname = "test"\n')
    semantic_dir = building / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = building / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    time_dimension = (
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n\n"
        if with_time
        else ""
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "import marivo.datasource as md\n"
        "\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "\n"
        f"{time_dimension}"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
    )
    try:
        os.replace(building, cache)
    except OSError:
        if not cache.exists():
            raise
    finally:
        if building.exists():
            shutil.rmtree(building)
    return cache


def bootstrap_sales_project_from_template(tmp_path: Path, *, with_time: bool = True) -> None:
    """Copy the cached sales project template into tmp_path/.

    Faster than writing files individually per test.
    """
    src = sales_project_template(with_time=with_time)
    shutil.copytree(src / "models", tmp_path / "models")
    shutil.copy2(src / "marivo.toml", tmp_path / "marivo.toml")


# ---------------------------------------------------------------------------
# Lightweight MetricFrame helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Authoring session helper (metric-split foundation tests)
# ---------------------------------------------------------------------------


@contextmanager
def authoring_session(*, domain: str):
    """Context manager that enters a LoaderContext with a default domain.

    Exposes helpers for declaring measure dimensions and inspecting pending
    metric IR objects. Used by tests/test_metric_split_foundation.py.
    """
    from marivo.semantic import authoring
    from marivo.semantic.ir import MetricIR
    from marivo.semantic.loader import _LOADER_CTX, LoaderContext

    ctx = LoaderContext(default_domain=domain)
    _LOADER_CTX.set(ctx)
    try:

        class _Session:
            @staticmethod
            def measure(*, entity: str, name: str, additivity: Any = None) -> Any:
                """Declare a measure and return its exact measure ref."""
                decorator = authoring.measure(
                    entity=entity, name=name, additivity=additivity or "additive"
                )

                # Apply the decorator to a dummy function that returns an ibis-like expression.
                def _dummy_body(table: Any) -> Any:
                    return getattr(table, name)

                return decorator(_dummy_body)

            @staticmethod
            def pending_metric(semantic_id: str) -> MetricIR:
                """Retrieve a pending MetricIR by semantic_id."""
                for pending in ctx.pending_definitions:
                    if (
                        isinstance(pending.definition, MetricIR)
                        and pending.definition.semantic_id == semantic_id
                    ):
                        return pending.definition
                raise KeyError(f"no pending MetricIR with semantic_id={semantic_id!r}")

            @staticmethod
            def pending_dimension(semantic_id: str) -> Any:
                """Retrieve a pending DimensionIR by semantic_id."""
                from marivo.semantic.ir import DimensionIR

                for pending in ctx.pending_definitions:
                    if (
                        isinstance(pending.definition, DimensionIR)
                        and pending.definition.semantic_id == semantic_id
                    ):
                        return pending.definition
                raise KeyError(f"no pending DimensionIR with semantic_id={semantic_id!r}")

        yield _Session()
    finally:
        _LOADER_CTX.set(None)


# ---------------------------------------------------------------------------
# Inline semantic project loader (metric-split resolution tests)
# ---------------------------------------------------------------------------


@contextmanager
def load_inline_semantic(
    source: str,
    *,
    domain: str = "test",
    expect_errors: bool = False,
):
    """Write an inline semantic source to a temp project and load it.

    Creates a minimal project with a single domain file containing *source*,
    plus a DuckDB datasource.  Returns the ``LoadResult`` from
    ``load_project``.

    When *expect_errors* is True, suppress the ``SemanticLoadError`` that
    ``assembly_validate`` would raise and return the result with errors
    attached instead.
    """
    from marivo.semantic.loader import load_project

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
        semantic_dir = tmp_path / "models" / "semantic" / domain
        semantic_dir.mkdir(parents=True)
        datasource_dir = tmp_path / "models" / "datasources"
        datasource_dir.mkdir(parents=True)
        (datasource_dir / "wh.py").write_text(
            "import marivo.datasource as md\nmd.duckdb(name='wh', path=':memory:')\n"
        )
        (semantic_dir / "__init__.py").write_text("")
        (semantic_dir / "_domain.py").write_text(
            f"import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name={domain!r}, owner='Mina Zhang', default=True)\n"
        )
        (semantic_dir / "models.py").write_text(source)
        result = load_project(semantic_dir.parent)
        yield result


# ---------------------------------------------------------------------------
# Multi-metric sales project (two entities, three metrics)
# ---------------------------------------------------------------------------


def bootstrap_multi_metric_sales_project(tmp_path: Path) -> None:
    """Semantic project with two entities and three simple metrics.

    orders: order_date (day), region dimension, revenue + order_count metrics.
    users: signup_date (day), user_count metric. Same warehouse datasource.
    """
    (tmp_path / "marivo.toml").write_text('[project]\nname = "test"\n')
    semantic_dir = tmp_path / "models" / "semantic" / "sales"
    semantic_dir.mkdir(parents=True)
    datasource_dir = tmp_path / "models" / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        "import marivo.datasource as md\nmd.duckdb(name='warehouse', path=':memory:')\n"
    )
    (semantic_dir / "__init__.py").write_text("")
    (semantic_dir / "_domain.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"
    )
    (semantic_dir / "datasets.py").write_text(
        "import marivo.datasource as md\nimport marivo.semantic as ms\n"
        "\n"
        "warehouse = ms.ref.datasource('warehouse')\n"
        "\n"
        "orders = ms.entity(name='orders', datasource=warehouse, source=md.table('orders'))\n"
        "users = ms.entity(name='users', datasource=warehouse, source=md.table('users'))\n"
        "\n"
        "@ms.time_dimension(entity=orders, granularity='day')\n"
        "def order_date(orders):\n"
        "    return orders.created_at.cast('date')\n"
        "\n"
        "@ms.time_dimension(entity=users, granularity='day')\n"
        "def signup_date(users):\n"
        "    return users.signed_up_at.cast('date')\n"
        "\n"
        "@ms.dimension(entity=orders)\n"
        "def region(orders):\n"
        "    return orders.region.upper()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity='additive', name='order_count', )\n"
        "def order_count(orders):\n"
        "    return orders.order_id.count()\n"
        "\n"
        "@ms.metric(entities=[users], additivity='additive', name='user_count', )\n"
        "def user_count(users):\n"
        "    return users.user_id.count()\n"
        "\n"
        "amount_col = ms.measure_column(name='amount_col', entity=orders, column='amount', additivity='additive', unit='USD')\n"
        "revenue_agg = ms.aggregate(name='revenue_agg', measure=amount_col, agg='sum')\n"
        "cumulative_revenue = ms.cumulative(name='cumulative_revenue', base=revenue_agg, over=order_date)\n"
    )


def seed_multi_metric_tables(con: ibis.duckdb.DuckDBBackend) -> None:
    """Seed orders and users tables matching bootstrap_multi_metric_sales_project."""
    con.raw_sql(
        "CREATE TABLE orders (order_id INTEGER, created_at DATE, "
        "amount DOUBLE, region VARCHAR, user_id INTEGER)"
    )
    con.raw_sql(
        "INSERT INTO orders VALUES "
        "(1, DATE '2026-07-01', 10.0, 'north', 100),"
        "(2, DATE '2026-07-02', 20.0, 'north', 100),"
        "(3, DATE '2026-07-02', 30.0, 'south', 200)"
    )
    con.raw_sql("CREATE TABLE users (user_id INTEGER, signed_up_at DATE)")
    con.raw_sql("INSERT INTO users VALUES (100, DATE '2026-07-01'), (200, DATE '2026-07-03')")


# ---------------------------------------------------------------------------
# Commerce order lifecycle project (Events + StateModel over DuckDB)
# ---------------------------------------------------------------------------

LIFECYCLE_MODEL_REF = "commerce.order_lifecycle"
LIFECYCLE_METRIC_REF = "commerce.order_count"

# (event_id, order_id, event_type, event_time)
LIFECYCLE_BASE_EVENTS: tuple[tuple[str, ...], ...] = (
    ("e1", "o1", "created", "2026-06-15 00:00:00"),
    ("e2", "o1", "paid", "2026-07-05 00:00:00"),
    ("e3", "o1", "paid", "2026-07-10 00:00:00"),
    ("e4", "o1", "closed", "2026-07-20 00:00:00"),
    ("e5", "o1", "paid", "2026-07-25 00:00:00"),
    ("e6", "o2", "created", "2026-07-10 00:00:00"),
    ("e7", "o2", "closed", "2026-07-25 00:00:00"),
)

# (order_id, region, created_at), with optional funnel driver columns.
LIFECYCLE_BASE_ORDERS: tuple[tuple[str, ...], ...] = (
    ("o1", "east", "2026-06-15"),
    ("o2", "west", "2026-07-10"),
)

FUNNEL_BASE_ORDERS: tuple[tuple[str, ...], ...] = (
    ("o1", "east", "2026-07-02", "paid", "pro"),
    ("o2", "west", "2026-07-10", "organic", "free"),
    ("o3", "east", "2026-07-11", "paid", "free"),
)

FUNNEL_BASE_EVENTS: tuple[tuple[str, ...], ...] = (
    ("f1", "o1", "created", "2026-07-02 00:00:00"),
    ("f2", "o1", "paid", "2026-07-05 00:00:00"),
    ("f3", "o2", "created", "2026-07-10 00:00:00"),
    ("f4", "o3", "created", "2026-07-11 00:00:00"),
    ("f5", "o3", "paid", "2026-07-12 00:00:00"),
)

LIFECYCLE_WATERMARK = {
    "complete_through": "2026-08-01T00:00:00Z",
    "authority": "warehouse_reconciliation",
    "observed_at": "2026-08-01T01:00:00Z",
    "source_revision": "fixture-v1",
}

_LIFECYCLE_DOMAIN = """\
import marivo.semantic as ms
ms.domain(name="commerce", owner="Analytics", default=True)
"""

_LIFECYCLE_OBJECTS = '''\
import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.ref.datasource("warehouse")
orders = ms.entity(
    name="orders", datasource=warehouse, source=md.table("orders"),
    primary_key=["order_id"],
    ai_context=ms.ai_context(business_definition="One row per order."),
)
event_log = ms.entity(
    name="event_log", datasource=warehouse, source=md.table("event_log"),
    primary_key=["event_id"],
    ai_context=ms.ai_context(business_definition="One row per order event."),
)
order_id = ms.dimension_column(name="order_id", entity=orders, column="order_id")
region = ms.dimension_column(name="region", entity=orders, column="region")
acquisition_channel = ms.dimension_column(
    name="acquisition_channel", entity=orders, column="acquisition_channel"
)
plan_tier = ms.dimension_column(name="plan_tier", entity=orders, column="plan_tier")
created_date = ms.time_dimension_column(
    name="created_date", entity=orders, column="created_at",
    granularity="day", is_default=True,
)
event_id = ms.dimension_column(name="event_id", entity=event_log, column="event_id")
event_order_id = ms.dimension_column(
    name="order_id", entity=event_log, column="order_id"
)
event_type = ms.dimension_column(name="event_type", entity=event_log, column="event_type")
event_time = ms.time_dimension_column(
    name="event_time", entity=event_log, column="event_time",
    granularity="second", parse=ms.timestamp(timezone="UTC"), is_default=True,
)
event_to_order = ms.relationship(
    name="event_to_order", from_entity=event_log, to_entity=orders,
    keys=[ms.join_on(event_order_id, order_id)],
)

@ms.metric(
    entities=[orders], additivity="additive", name="order_count",
    ai_context=ms.ai_context(business_definition="Distinct orders."),
)
def order_count(orders):
    """Count orders."""
    return orders.order_id.count()

@ms.event(
    name="order_created", identity=(event_id,), occurred_at=event_time,
    participants=(
        ms.participant(name="order", path=(event_to_order,), cardinality="one"),
    ),
    ai_context=ms.ai_context(business_definition="An order was created."),
)
def order_created(rows):
    return ms.bind(event_type, rows) == "created"

@ms.event(
    name="payment_captured", identity=(event_id,), occurred_at=event_time,
    participants=(
        ms.participant(name="order", path=(event_to_order,), cardinality="one"),
    ),
    ai_context=ms.ai_context(business_definition="Payment was captured."),
)
def payment_captured(rows):
    return ms.bind(event_type, rows) == "paid"

@ms.event(
    name="order_closed", identity=(event_id,), occurred_at=event_time,
    participants=(
        ms.participant(name="order", path=(event_to_order,), cardinality="one"),
    ),
    ai_context=ms.ai_context(business_definition="An order was closed."),
)
def order_closed(rows):
    return ms.bind(event_type, rows) == "closed"

created = ms.lifecycle_state(name="created", initial=True)
paid = ms.lifecycle_state(name="paid")
closed = ms.lifecycle_state(name="closed", terminal=True)
order_lifecycle = ms.state_model(
    name="order_lifecycle",
    subject=orders,
    states=(created, paid, closed),
    transitions=(
        ms.inception(on=order_created),
        ms.transition(from_state=created, on=payment_captured, to_state=paid),
        ms.transition(from_state=created, on=order_closed, to_state=closed),
        ms.transition(from_state=paid, on=order_closed, to_state=closed),
    ),
    ai_context=ms.ai_context(business_definition="Commercial order lifecycle."),
)
'''


def lifecycle_project_files() -> dict[str, str]:
    """Return the commerce lifecycle project for ``semantic_project_factory``."""
    return {
        "commerce/_domain.py": _LIFECYCLE_DOMAIN,
        "commerce/objects.py": _LIFECYCLE_OBJECTS,
    }


def _sql_values(rows: tuple[tuple[str, ...], ...]) -> str:
    return ", ".join("(" + ", ".join(f"'{item}'" for item in row) + ")" for row in rows)


def seed_lifecycle_backend(
    *,
    events: tuple[tuple[str, ...], ...] = LIFECYCLE_BASE_EVENTS,
    orders: tuple[tuple[str, ...], ...] = LIFECYCLE_BASE_ORDERS,
    watermark_events: frozenset[str] = frozenset(),
) -> Any:
    """Return a DuckDB backend seeded for the commerce lifecycle project."""
    backend = ibis.duckdb.connect(":memory:")
    normalized_orders = tuple(
        (*row, "unknown", "unknown") if len(row) == 3 else row for row in orders
    )
    backend.raw_sql(
        "CREATE TABLE orders ("
        "order_id VARCHAR, region VARCHAR, created_at DATE, "
        "acquisition_channel VARCHAR, plan_tier VARCHAR)"
    )
    backend.raw_sql(f"INSERT INTO orders VALUES {_sql_values(normalized_orders)}")
    backend.raw_sql(
        "CREATE TABLE event_log ("
        "event_id VARCHAR, order_id VARCHAR, event_type VARCHAR, event_time TIMESTAMP)"
    )
    backend.raw_sql(f"INSERT INTO event_log VALUES {_sql_values(events)}")
    if watermark_events:

        def provider(request: Any) -> Any:
            return LIFECYCLE_WATERMARK if request.event_ref.path in watermark_events else None

        backend.marivo_event_watermark = provider
    return backend


def pattern_step_for_tests(key: str) -> Any:
    """Build one standalone commerce PatternStep for pure-engine tests."""
    import marivo.analysis as mv
    import marivo.semantic as ms

    definitions = {
        "cart": ("commerce.order_created", "order"),
        "payment": ("commerce.payment_captured", "order"),
        "refund": ("commerce.order_closed", "order"),
    }
    event_path, role = definitions[key]
    return mv.step(
        participant=ms.participant_role(event=ms.ref.event(event_path), name=role),
        key=key,
    )
