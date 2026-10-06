"""Shared lightweight fixtures for Python-native analysis tests."""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

import duckdb
import ibis

if TYPE_CHECKING:
    from marivo.analysis.core.graph import MethodNode
    from marivo.analysis.materialization.graph_store import GraphArtifact
    from marivo.analysis.session.core import Session
    from marivo.semantic.catalog import SemanticCatalog

# ---------------------------------------------------------------------------
# Named DuckDB templates (versioned, cached in /tmp)
# ---------------------------------------------------------------------------
# Bump the version string when seeded schema or rows change so cached
# copies rebuild automatically.

_SALES_ORDERS_V = "v1"
_AUTHORING_EVIDENCE_V = "v2"


# The S0 DSL journeys share declarations, but each journey owns its source rows.
DslScenario = Literal[
    "j1",
    "j2",
    "j3",
    "j3_weighting",
    "j4",
    "j4_ties",
    "empty_domain",
    "empty_group",
    "zero_denominator",
    "null_classification",
    "missing_key",
    "nonfinite",
    "overflow",
    "tuple_union",
]


@dataclass(frozen=True)
class DslNames:
    domain: str = "sales"
    customer: str = "customer"
    order: str = "order"
    order_line: str = "order_line"
    customer_id: str = "customer_id"
    order_id: str = "order_id"
    line_id: str = "line_id"
    region: str = "region"
    channel: str = "channel"
    status: str = "status"
    ordered_at: str = "ordered_at"
    amount: str = "amount"
    line_amount: str = "line_amount"
    buyer: str = "order_buyer"
    line_order: str = "line_order"
    revenue: str = "revenue"
    order_count: str = "order_count"
    line_revenue: str = "line_revenue"
    aov: str = "aov_from_lines"


DSL_NAMES = DslNames()


@dataclass(frozen=True)
class DslRows:
    customers: tuple[tuple[str | None, str | None], ...]
    orders: tuple[tuple[str, str | None, str | None, str, str, int | float], ...]
    lines: tuple[tuple[str, str | None, int | float], ...]


@dataclass(frozen=True)
class DslCase:
    scenario: DslScenario
    names: DslNames
    root: Path
    database_path: Path
    catalog: SemanticCatalog
    session: Session


def export_dsl_parquet_models(case: DslCase, project: Path) -> None:
    """Export fixture facts and bind authored Entities to local Parquet sources."""
    import pyarrow.parquet as pq

    source_files = project / "source_files"
    source_files.mkdir(exist_ok=True)
    backend = ibis.duckdb.connect(case.database_path)
    try:
        for name in (case.names.customer, case.names.order, case.names.order_line):
            path = source_files / f"{name}.parquet"
            pq.write_table(backend.table(name).to_pyarrow(), path)
            for model in (project / "models").rglob("*.py"):
                model.write_text(
                    model.read_text().replace(f"md.table({name!r})", f"md.parquet({str(path)!r})")
                )
    finally:
        backend.disconnect()


class DslCaseFactory(Protocol):
    def __call__(
        self,
        scenario: DslScenario,
        *,
        names: DslNames = DSL_NAMES,
        revenue_unit: str = "CNY",
    ) -> DslCase: ...


def analysis_dsl_project_files(
    names: DslNames, database_path: Path, *, revenue_unit: str = "CNY"
) -> dict[str, str]:
    """Return real authoring files for the inactive DSL journeys."""
    n = names
    models = f"""\
import marivo.datasource as md
import marivo.semantic as ms

warehouse = ms.ref.datasource('warehouse')
customer = ms.entity(name={n.customer!r}, datasource=warehouse,
                     source=md.table({n.customer!r}), primary_key=[{n.customer_id!r}])
orders = ms.entity(name={n.order!r}, datasource=warehouse,
                   source=md.table({n.order!r}), primary_key=[{n.order_id!r}])
lines = ms.entity(name={n.order_line!r}, datasource=warehouse,
                  source=md.table({n.order_line!r}), primary_key=[{n.line_id!r}])

customer_id = ms.dimension_column(name={n.customer_id!r}, entity=customer,
                                  column={n.customer_id!r})
order_customer_id = ms.dimension_column(name={n.customer_id!r}, entity=orders,
                                        column={n.customer_id!r})
order_id = ms.dimension_column(name={n.order_id!r}, entity=orders,
                               column={n.order_id!r})
line_order_id = ms.dimension_column(name={n.order_id!r}, entity=lines,
                                    column={n.order_id!r})
region = ms.dimension_column(name={n.region!r}, entity=customer,
                             column={n.region!r})
channel = ms.dimension_column(name={n.channel!r}, entity=orders,
                              column={n.channel!r})
status = ms.dimension_column(name={n.status!r}, entity=orders,
                             column={n.status!r})
ordered_at = ms.time_dimension_column(name={n.ordered_at!r}, entity=orders,
                                      column={n.ordered_at!r}, granularity='second',
                                      parse=ms.timestamp(timezone='UTC'))
amount = ms.measure_column(name={n.amount!r}, entity=orders,
                           column={n.amount!r}, additivity=ms.additive_all(),
                           unit={revenue_unit!r})
line_amount = ms.measure_column(name={n.line_amount!r}, entity=lines,
                                column={n.line_amount!r}, additivity=ms.additive_all(),
                                unit='CNY')
buyer = ms.relationship(name={n.buyer!r}, from_entity=orders, to_entity=customer,
                        keys=[ms.join_on(order_customer_id, customer_id)])
line_order = ms.relationship(name={n.line_order!r}, from_entity=lines, to_entity=orders,
                             keys=[ms.join_on(line_order_id, order_id)])
revenue = ms.aggregate(name={n.revenue!r}, measure=amount, agg='sum', time=ordered_at)
@ms.metric(name='opaque_revenue', entities=[orders], time=ordered_at,
           unit={revenue_unit!r}, additivity=ms.additive_all(),
           nulls=ms.nulls.ignore(), empty=ms.empty.null())
def opaque_revenue(order_rows):
    return order_rows.{n.amount}.sum()
order_count = ms.count(name={n.order_count!r}, entity=orders, time=ordered_at)
line_revenue = ms.aggregate(name={n.line_revenue!r}, measure=line_amount, agg='sum',
                            time=ordered_at, time_via=(line_order,),
                            nulls=ms.nulls.ignore(), empty=ms.empty.zero())
aov = ms.ratio(name={n.aov!r}, numerator=line_revenue, denominator=order_count,
               zero_denominator=ms.zero_denominator.undefined())
"""
    return {
        "datasources/warehouse.py": (
            "import marivo.datasource as md\n"
            f"md.duckdb(name='warehouse', path={str(database_path)!r})\n"
        ),
        f"{n.domain}/_domain.py": (
            "import marivo.semantic as ms\n"
            f"ms.domain(name={n.domain!r}, owner='Fixture', default=True)\n"
        ),
        f"{n.domain}/models.py": models,
    }


_AUGUST = "2026-08-15T12:00:00+00:00"


def analysis_dsl_rows(scenario: DslScenario) -> DslRows:
    """Return source facts, without any expected analysis result."""
    if scenario == "j1":
        return DslRows(
            (("A", "east"), ("B", "east"), ("C", "south"), ("D", "west")),
            (
                ("j1_july", "A", "web", "paid", "2026-07-31T23:59:59+00:00", 77),
                ("j1_a", "A", "web", "paid", "2026-08-01T00:00:00+00:00", 450),
                ("j1_b", "B", "mobile", "paid", _AUGUST, 150),
                ("j1_c", "C", "web", "paid", "2026-08-31T23:59:59+00:00", 400),
                ("j1_september", "A", "mobile", "paid", "2026-09-01T00:00:00+00:00", 99),
            ),
            (),
        )
    if scenario == "j2":
        return DslRows(
            (("A", "east"), ("B", "east"), ("C", "south"), ("D", "west")),
            (
                ("j2_ja", "A", "web", "paid", "2026-07-10T12:00:00+00:00", 100),
                ("j2_jb", "B", "web", "paid", "2026-07-10T12:00:00+00:00", 100),
                ("j2_jc", "C", "web", "paid", "2026-07-10T12:00:00+00:00", 50),
                ("j2_jd", "D", "web", "paid", "2026-07-10T12:00:00+00:00", 0),
                ("j2_aa", "A", "web", "paid", "2026-08-01T00:00:00+00:00", 60),
                ("j2_ab", "B", "web", "paid", _AUGUST, 120),
                ("j2_ac", "C", "web", "paid", _AUGUST, 0),
                ("j2_ad", "D", "web", "paid", _AUGUST, 0),
                ("j2_sa", "A", "web", "paid", "2026-09-01T00:00:00+00:00", 30),
                ("j2_sb", "B", "web", "paid", "2026-09-10T12:00:00+00:00", 200),
                ("j2_sc", "C", "web", "paid", "2026-09-10T12:00:00+00:00", 0),
            ),
            (),
        )
    if scenario == "j3":
        return DslRows(
            (("A", "east"), ("B", "east")),
            (
                ("j3_aw", "A", "web", "paid", _AUGUST, 0),
                ("j3_am", "A", "mobile", "paid", _AUGUST, 0),
                ("j3_bw1", "B", "web", "paid", _AUGUST, 0),
                ("j3_bw2", "B", "web", "paid", _AUGUST, 0),
            ),
            (
                ("j3_l1", "j3_aw", 40),
                ("j3_l2", "j3_aw", 60),
                ("j3_l3", "j3_bw1", 20),
                ("j3_l4", "j3_bw2", 40),
            ),
        )
    if scenario == "j3_weighting":
        orders = (
            *((f"j3_a_{index}", "A", "web", "paid", _AUGUST, 0) for index in range(100)),
            ("j3_b", "B", "web", "paid", _AUGUST, 0),
        )
        lines = (
            *((f"j3_line_{index}", f"j3_a_{index}", 1) for index in range(100)),
            ("j3_line_b", "j3_b", 100),
        )
        return DslRows((("A", "east"), ("B", "west")), orders, lines)
    if scenario in ("j4", "j4_ties"):
        counts = (4, 1, 3, 2) if scenario == "j4" else (1, 1, 3, 2)
        totals = (1.0, 2.0, 4.0, 8.0) if scenario == "j4" else (1.0, 1.0, 2.0, 3.0)
        rank_orders = tuple(
            (
                f"{scenario}_{customer}_{index}",
                customer,
                "web",
                "paid",
                _AUGUST,
                total if index == 0 else 0.0,
            )
            for customer, count, total in zip("ABCD", counts, totals, strict=True)
            for index in range(count)
        )
        return DslRows(tuple((customer, "east") for customer in "ABCD"), rank_orders, ())
    if scenario == "empty_domain":
        return DslRows((), (), ())
    if scenario in ("empty_group", "tuple_union"):
        customers = (
            (("A", "east"), ("B", "west")) if scenario == "empty_group" else (("A", "east"),)
        )
        orders = (
            (("group_a", "A", "web", "paid", _AUGUST, 10),)
            if scenario == "empty_group"
            else (
                ("tuple_web", "A", "web", "paid", _AUGUST, 0),
                ("tuple_mobile", "A", "mobile", "cancelled", _AUGUST, 0),
            )
        )
        lines = () if scenario == "empty_group" else (("tuple_line", "tuple_web", 40),)
        return DslRows(customers, orders, lines)
    if scenario == "zero_denominator":
        return DslRows((("A", "east"),), (), ())
    if scenario == "null_classification":
        return DslRows(
            (("A", None), ("B", "east")),
            (("null_channel", "A", None, "paid", _AUGUST, 100),),
            (),
        )
    if scenario == "missing_key":
        return DslRows(
            ((None, "east"), ("A", "east")),
            (("missing_customer", None, "web", "paid", _AUGUST, 10),),
            (("missing_order", None, 10),),
        )
    if scenario == "nonfinite":
        return DslRows(
            (("A", "east"),),
            (
                ("nan", "A", "web", "paid", _AUGUST, float("nan")),
                ("infinity", "A", "web", "paid", _AUGUST, float("inf")),
            ),
            (),
        )
    if scenario == "overflow":
        return DslRows(
            (("A", "east"),),
            (
                ("maximum", "A", "web", "paid", _AUGUST, 2**63 - 1),
                ("one_more", "A", "web", "paid", _AUGUST, 1),
            ),
            (),
        )
    raise ValueError(f"Unknown DSL fixture scenario: {scenario}")


def seed_analysis_dsl_database(
    path: Path, names: DslNames, rows: DslRows, *, float_amount: bool
) -> None:
    """Write one isolated DuckDB source file and close its only seed connection."""

    def quoted(value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    n = names
    numeric_type = "DOUBLE" if float_amount else "BIGINT"
    conn = duckdb.connect(str(path))
    try:
        conn.execute("SET threads = 1")
        conn.execute(
            f"CREATE TABLE {quoted(n.customer)} ("
            f"{quoted(n.customer_id)} VARCHAR, {quoted(n.region)} VARCHAR)"
        )
        conn.execute(
            f"CREATE TABLE {quoted(n.order)} ("
            f"{quoted(n.order_id)} VARCHAR, {quoted(n.customer_id)} VARCHAR, "
            f"{quoted(n.channel)} VARCHAR, {quoted(n.status)} VARCHAR, "
            f"{quoted(n.ordered_at)} TIMESTAMPTZ, {quoted(n.amount)} {numeric_type})"
        )
        conn.execute(
            f"CREATE TABLE {quoted(n.order_line)} ("
            f"{quoted(n.line_id)} VARCHAR, {quoted(n.order_id)} VARCHAR, "
            f"{quoted(n.line_amount)} {numeric_type})"
        )
        if rows.customers:
            conn.executemany(f"INSERT INTO {quoted(n.customer)} VALUES (?, ?)", rows.customers)
        if rows.orders:
            conn.executemany(
                f"INSERT INTO {quoted(n.order)} VALUES (?, ?, ?, ?, ?, ?)", rows.orders
            )
        if rows.lines:
            conn.executemany(f"INSERT INTO {quoted(n.order_line)} VALUES (?, ?, ?)", rows.lines)
    finally:
        conn.close()


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
            "amount = ms.measure_column(name='amount', entity=events, column='amount', additivity=ms.additive_all(), unit='USD')\n"
            "user_id = ms.measure_column(name='user_id', entity=events, column='user_id', additivity=ms.non_additive())\n"
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
        "@ms.metric(entities=[orders], additivity=ms.additive_all(), name='revenue', )\n"
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
        "@ms.metric(entities=[orders], additivity=ms.additive_all(), name='revenue', )\n"
        "def revenue(orders):\n"
        "    return orders.amount.sum()\n"
        "\n"
        "@ms.metric(entities=[orders], additivity=ms.additive_all(), name='order_count', )\n"
        "def order_count(orders):\n"
        "    return orders.order_id.count()\n"
        "\n"
        "@ms.metric(entities=[users], additivity=ms.additive_all(), name='user_count', )\n"
        "def user_count(users):\n"
        "    return users.user_id.count()\n"
        "\n"
        "amount_col = ms.measure_column(name='amount_col', entity=orders, column='amount', additivity=ms.additive_all(), unit='USD')\n"
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
    entities=[orders], additivity=ms.additive_all(), name="order_count",
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


def graph_count_continuation(record: GraphArtifact) -> MethodNode:
    """Build a current-row count from an exact private v7 Artifact."""
    from marivo.analysis.core.graph import Edge, FixedLeaf, method_node
    from marivo.analysis.core.model import DomainSignature
    from marivo.analysis.core.rules import RowState
    from marivo.analysis.methods.physical import FixedShape, NoTime, ScalarType
    from marivo.analysis.refs import ArtifactRef

    leaf = FixedLeaf(
        ArtifactRef(record.artifact_ref),
        record.descriptor.definition_fingerprint,
        record.descriptor.signature,
        ScalarType("int64"),
        FixedShape(NoTime()),
    )
    domain = DomainSignature(leaf.signature.domain.binding, "singleton", (), (), "all-products")
    return method_node(
        (Edge("quantity", leaf),),
        RowState("count", domain, "current-count", "count_all"),
        value_type=ScalarType("int64"),
    )


def run_ids(session: Session) -> set[str]:
    identities: set[str] = set()
    cursor: str | None = None
    while True:
        page = session.runs(limit=100, cursor=cursor)
        identities.update(item.run_id for item in page.items)
        cursor = page.next_cursor
        if cursor is None:
            return identities
