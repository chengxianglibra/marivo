"""Window endpoints reevaluate complete native formulas over actual source rows."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo._definition.windows import TrailingDuration
from marivo.analysis.binding.windows import _reset
from marivo.analysis.core.data_schema import CalendarDataAxis
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.core.frozen_calendar import read_calendar_directory
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject


@pytest.fixture
def window_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "windows.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("SET threads = 1")
        connection.execute(
            "CREATE TABLE orders (id BIGINT, customer BIGINT, ordered_at TIMESTAMPTZ, amount BIGINT, region VARCHAR, channel VARCHAR)"
        )
        connection.execute(
            "INSERT INTO orders (id, customer, ordered_at, amount) VALUES (1, 7, '2026-09-20 00:00:00+00', 100), (2, 7, '2026-10-07 00:00:00+00', 20)"
        )
    datasources = tmp_path / "models" / "datasources"
    datasources.mkdir(parents=True)
    (datasources / "warehouse.py").write_text(
        f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n"
    )
    semantic_project_factory(
        {
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales", owner="Analytics", default=True)\n',
            "sales/objects.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name="orders", datasource=ms.ref.datasource("warehouse"), source=md.table("orders"), primary_key=["id"])
amount_ref = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all(), unit="CNY")
at_ref = ms.time_dimension_column(name="ordered_at", entity=orders, column="ordered_at", granularity="day", is_default=True)
sample_ref = ms.time_dimension_column(name="sample_at", entity=orders, column="ordered_at", granularity="hour", parse=ms.datetime(timezone="UTC", sample_interval=(1, "hour")))
customer_ref = ms.dimension_column(name="customer", entity=orders, column="customer")
region_ref = ms.dimension_column(name="region", entity=orders, column="region")
channel_ref = ms.dimension_column(name="channel", entity=orders, column="channel")
frozen = ms.finalize()
amount = frozen.resolve(amount_ref)
customer = frozen.resolve(customer_ref)
@ms.metric()
def revenue():
    return amount.sum()
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("windows", timezone="UTC")


def _weekly_facts(tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders VALUES "
            "(1,6,'2020-12-15 00:00:00+00',5,'old','web'),"
            "(2,7,'2020-12-22 00:00:00+00',10,'north','web'),"
            "(3,8,'2020-12-23 00:00:00+00',30,'south','shop'),"
            "(4,7,'2020-12-29 00:00:00+00',200,'gap','only'),"
            "(5,7,'2021-01-05 00:00:00+00',20,'north','web'),"
            "(6,7,'2021-01-06 00:00:00+00',30,'north','web'),"
            "(7,9,'2021-01-07 00:00:00+00',100,'new','shop')"
        )


def test_sparse_shift_observes_raw_contributions_and_nominal_windows(
    window_session: Session, tmp_path: Path
) -> None:
    _weekly_facts(tmp_path)
    session = window_session
    amount = session.catalog.fields.get("sales.orders.amount")
    customer = session.catalog.fields.get("sales.orders.customer")
    calendar = (
        session.time_scope(start="2019-12-23", end="2020-01-06")
        .by(mv.grain("week"))
        .shift(mv.offset(years=1))
    )
    metrics = {
        "revenue": amount.sum(),
        "aov": amount.sum() / amount.count(),
        "customers": customer.count_distinct(),
        "median": amount.quantile(q=0.5),
    }
    ordinary = session.observe(metrics, by=(calendar,)).compute()
    values = ordinary.to_pandas()
    assert values.iloc[:, 0].tolist() == [0, 1]
    assert values[("value", "revenue")].tolist() == [40, 150]
    assert values[("value", "aov")].tolist() == [20.0, 50.0]
    assert values[("value", "customers")].tolist() == [2, 2]
    assert values[("value", "median")].tolist() == [20.0, 30.0]
    trailing = (
        session.observe(
            {name: metric.window(mv.trailing_periods(count=2)) for name, metric in metrics.items()},
            by=(calendar,),
        )
        .compute()
        .to_pandas()
    )
    assert trailing[("value", "revenue")].tolist() == [45, 350]
    assert trailing[("value", "aov")].tolist() == [15.0, 87.5]
    assert trailing[("value", "customers")].tolist() == [3, 2]
    assert trailing[("value", "median")].tolist() == [10.0, 65.0]
    axis = ordinary._schema.axes[0]
    assert isinstance(axis, CalendarDataAxis) and axis.frozen is not None
    assert axis.frozen.direct is not None
    assert isinstance(ordinary._backing, MemoryFrameBacking)
    assert {part.name for part in ordinary._backing.data.copy_attachments()} == {
        axis.frozen.directory_name,
        axis.frozen.direct.axis.frozen.directory_name,
        axis.frozen.direct.mapping_name,
    }


@pytest.mark.parametrize("windowed", [False, True])
def test_empty_shift_is_typed_zero_endpoint_observation(
    window_session: Session, windowed: bool
) -> None:
    session = window_session
    amount = session.catalog.fields.get("sales.orders.amount")
    calendar = (
        session.time_scope(start="2020-12-28", end="2021-01-04")
        .by(mv.grain("week"))
        .shift(mv.offset(years=-1))
    )
    metric = amount.count().divide(amount.count() * 0, zero_denominator="error")
    if windowed:
        metric = metric.window(mv.trailing_periods(count=2))
    sampled = session.catalog.fields.get("sales.orders.sample_at")
    result = session.observe(
        {"aov": metric, "sampled": amount.sum(time=sampled).per(sampled).mean()}, by=(calendar,)
    ).compute()
    frame = result.to_pandas()
    assert frame.empty
    assert result.output_names == ("aov", "sampled")
    axis = result._schema.axes[0]
    assert isinstance(axis, CalendarDataAxis) and axis.frozen is not None
    assert axis.frozen.direct is not None
    assert isinstance(result._backing, MemoryFrameBacking)
    tables = {part.name: part for part in result._backing.data.copy_attachments()}
    assert tables[axis.frozen.directory_name].rows.empty
    assert tables[axis.frozen.direct.mapping_name].rows["reason"].tolist() == ["period_absent"]
    with pytest.raises(AnalysisError, match="denominator"):
        session.observe({"bad": amount.count().divide(0, zero_denominator="error")}).compute()


def test_sparse_calendar_joint_groups_and_fixed_populations(
    window_session: Session, tmp_path: Path
) -> None:
    _weekly_facts(tmp_path)
    session = window_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    calendar = (
        session.time_scope(start="2019-12-23", end="2020-01-06")
        .by(mv.grain("week"))
        .shift(mv.offset(years=1))
    )
    strict = amount.sum().divide(amount.count() - 1, zero_denominator="error")
    assert session.observe({"strict": strict}, by=(calendar,)).compute().to_pandas()[
        ("value", "strict")
    ].tolist() == [40.0, 75.0]
    rows = (
        session.observe({"total": amount.sum()}, by=(calendar, region, channel))
        .compute()
        .to_pandas()
    )
    assert set(rows.iloc[:, 1:3].itertuples(index=False, name=None)) == {
        ("north", "web"),
        ("south", "shop"),
        ("new", "shop"),
    }
    assert len(rows) == 6
    gap = session.time_scope(start="2020-12-28", end="2021-01-04").by(mv.grain("week"))
    with pytest.raises(AnalysisError, match="denominator"):
        session.observe({"strict": strict}, by=(gap,)).compute()
    pairs = mv.coordinates(by=(region, channel), keys=(("north", "web"), ("south", "shop")))
    explicit = (
        session.observe({"total": amount.sum()}, by=mv.product(calendar, pairs))
        .compute()
        .to_pandas()
    )
    assert explicit.iloc[:, :3].to_records(index=False).tolist() == [
        (0, "north", "web"),
        (0, "south", "shop"),
        (1, "north", "web"),
        (1, "south", "shop"),
    ]
    assert explicit[("value", "total")].to_dict() == {0: 10, 1: 30, 2: 50, 3: None}
    orders = session.catalog.entities.get("sales.orders")
    selected = mv.coordinates(by=(mv.identity(orders),), keys=(((2,),), ((5,),)))
    filtered = (
        session.observe(
            {"total": amount.sum(), "history": amount.sum().window(mv.trailing_periods(count=2))},
            by=(calendar,),
            where=mv.in_population(selected),
        )
        .compute()
        .to_pandas()
    )
    assert filtered[("value", "total")].tolist() == [10, 20]
    assert filtered[("value", "history")].tolist() == [10, 20]
    at = session.catalog.fields.get("sales.orders.ordered_at")
    hard = (
        session.observe(
            {"history": amount.sum().window(mv.trailing_periods(count=2))},
            by=(calendar,),
            where=at >= datetime(2021, 1, 1, tzinfo=timezone.utc),
        )
        .compute()
        .to_pandas()
    )
    assert hard[("value", "history")].to_dict() == {0: None, 1: 150}


def test_window_recomputes_history_ratio_and_distinct(window_session: Session) -> None:
    session = window_session
    revenue = session.catalog.metrics.get("sales.revenue")
    customer = session.catalog.fields.get("sales.orders.customer")
    amount = session.catalog.fields.get("sales.orders.amount")
    scope = session.time_scope(start="2026-10-07", end="2026-10-09")
    values = (
        session.observe(
            {
                "revenue": revenue.window(mv.all_history()),
                "aov": (revenue / amount.count()).window(mv.all_history()),
                "customers": customer.count_distinct().window(mv.all_history()),
            },
            by=(scope.by(mv.grain("day")),),
        )
        .compute()
        .to_pandas()
    )
    assert values[("value", "revenue")].tolist() == [120, 120]
    assert values[("value", "aov")].tolist() == [60.0, 60.0]
    assert values[("value", "customers")].tolist() == [1, 1]


def test_scalar_to_date_uses_endpoint_not_display_start(window_session: Session) -> None:
    session = window_session
    revenue = session.catalog.metrics.get("sales.revenue")
    metric = revenue.window(mv.to_date(grain=mv.grain("month")))
    for start in ("2026-09-01", "2026-10-01"):
        result = (
            session.observe(
                {"mtd": metric}, by=(session.time_scope(start=start, end="2026-10-08"),)
            )
            .compute()
            .to_pandas()
        )
        assert result[("value", "mtd")].tolist() == [20]


def test_exact_reset_boundary_and_elapsed_dst_duration() -> None:
    end = datetime(2026, 10, 1, tzinfo=timezone.utc)
    assert _reset(end, mv.grain("month"), "UTC") == (
        datetime(2026, 9, 1, tzinfo=timezone.utc),
        end,
    )
    # The New York spring-forward day has 23 elapsed hours; a duration stays 24.
    endpoint = datetime(2026, 3, 9, 4, tzinfo=timezone.utc)
    start, _ = _reset(endpoint, mv.grain("day"), "America/New_York")
    assert endpoint - start == timedelta(hours=23)
    duration = mv.trailing_duration(duration=timedelta(hours=24))
    assert isinstance(duration, TrailingDuration)
    assert endpoint - duration.duration != start


@pytest.mark.parametrize("calendar_first", [False, True])
def test_calendar_population_axis_order(window_session: Session, calendar_first: bool) -> None:
    session = window_session
    revenue = session.catalog.metrics.get("sales.revenue")
    customer = session.catalog.fields.get("sales.orders.customer")
    customers = mv.coordinates(by=(customer,), keys=((7,), (8,)))
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    domain = mv.product(calendar, customers) if calendar_first else mv.product(customers, calendar)
    completed = session.observe({"revenue": revenue}, by=domain).compute()
    result = completed.to_pandas()
    assert result[("value", "revenue")].to_dict() == {0: 20, 1: None, 2: None, 3: None}
    assert len(result) == 4
    days = [datetime(2026, 10, 7, tzinfo=timezone.utc), datetime(2026, 10, 8, tzinfo=timezone.utc)]
    assert result.iloc[:, 0].tolist() == ([0, 0, 1, 1] if calendar_first else [7, 7, 8, 8])
    assert result.iloc[:, 1].tolist() == ([7, 8, 7, 8] if calendar_first else [0, 1, 0, 1])
    calendar_axis = next(
        axis for axis in completed._schema.axes if isinstance(axis, CalendarDataAxis)
    )
    assert calendar_axis.frozen is not None
    assert isinstance(completed._backing, MemoryFrameBacking)
    part = next(
        part
        for part in completed._backing.data.copy_attachments()
        if part.name == calendar_axis.frozen.directory_name
    )
    directory = read_calendar_directory(calendar_axis, part.schema, part.rows)
    assert [bucket.actual_start for bucket in directory.buckets] == days


def test_per_calendar_population_retains_empty_samples(window_session: Session) -> None:
    session = window_session
    revenue = session.catalog.metrics.get("sales.revenue")
    customer = session.catalog.fields.get("sales.orders.customer")
    customers = mv.coordinates(by=(customer,), keys=((7,), (8,)))
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    result = (
        session.observe(
            {
                "mean": revenue.per(mv.product(customers, calendar)).mean(),
                "reverse_mean": revenue.per(mv.product(calendar, customers)).mean(),
            }
        )
        .compute()
        .to_pandas()
    )
    assert result[("value", "mean")].tolist() == [20.0]
    assert result[("value", "reverse_mean")].tolist() == [20.0]


def test_source_population_calendar_product(window_session: Session) -> None:
    session = window_session
    revenue = session.catalog.metrics.get("sales.revenue")
    customer = session.catalog.fields.get("sales.orders.customer")
    customers = session.members(session.catalog.require(ms.ref.entity("sales.orders"))).classify(
        by=(customer,)
    )
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    samples = mv.product(customers, calendar)
    result = session.observe({"revenue": revenue}, by=samples).compute().to_pandas()
    assert result[("value", "revenue")].to_dict() == {0: 20, 1: None}
    average = session.observe({"mean": revenue.per(samples).mean()}).compute().to_pandas()
    assert average[("value", "mean")].tolist() == [20.0]


def test_population_calendar_supplies_explicit_window_endpoints(window_session: Session) -> None:
    session = window_session
    revenue = session.catalog.metrics.get("sales.revenue")
    customer = session.catalog.fields.get("sales.orders.customer")
    customers = mv.coordinates(by=(customer,), keys=((7,),))
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    result = (
        session.observe(
            {"history": revenue.window(mv.all_history())}, by=mv.product(customers, calendar)
        )
        .compute()
        .to_pandas()
    )
    assert result[("value", "history")].tolist() == [120, 120]


def test_interval_selection_keeps_window_history_and_hard_fact_filter(
    window_session: Session,
) -> None:
    session = window_session
    amount = session.catalog.fields.get("sales.orders.amount")
    at = session.catalog.fields.get("sales.orders.ordered_at")
    customer = session.catalog.fields.get("sales.orders.customer")
    scope = session.time_scope(start="2026-10-07", end="2026-10-09")
    customers = mv.coordinates(by=(customer,), keys=((7,), (8,)))
    domain = mv.product(scope, customers)
    rows = (
        session.observe(
            {"ordinary": amount.sum(), "history": amount.sum().window(mv.all_history())},
            by=domain,
        )
        .compute()
        .to_pandas()
    )
    assert rows[("value", "ordinary")].to_dict() == {0: 20, 1: None}
    assert rows[("value", "history")].to_dict() == {0: 120, 1: None}
    restricted = (
        session.observe(
            {"history": amount.sum().window(mv.all_history())},
            by=(scope,),
            where=at >= session.instant("2026-10-01"),
        )
        .compute()
        .to_pandas()
    )
    assert restricted[("value", "history")].tolist() == [20]


def test_explicit_count_repeats_time_and_rejects_ineffective_window(
    window_session: Session,
) -> None:
    from marivo.analysis.errors import AnalysisError

    session = window_session
    entity = session.catalog.entities.get("sales.orders")
    people = mv.coordinates(by=(mv.identity(entity),), keys=(((1,),), ((2,),), ((999,),)))
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    rows = session.observe({"people": people.count()}, by=(calendar,)).compute().to_pandas()
    assert rows[("value", "people")].tolist() == [3, 3]
    with pytest.raises(AnalysisError, match="fact contributions for a window"):
        session.observe({"people": people.count().window(mv.all_history())}, by=(calendar,))


def test_interval_internal_sample_domain_keeps_its_own_time(window_session: Session) -> None:
    session = window_session
    amount = session.catalog.fields.get("sales.orders.amount")
    customer = session.catalog.fields.get("sales.orders.customer")
    scope = session.time_scope(start="2026-10-07", end="2026-10-09")
    customers = mv.coordinates(by=(customer,), keys=((7,), (8,)))
    rows = (
        session.observe({"mean": amount.sum().per(mv.product(scope, customers)).mean()})
        .compute()
        .to_pandas()
    )
    assert rows[("value", "mean")].tolist() == [20.0]
    assert len(rows.columns) == 1


@pytest.mark.parametrize("predicate", [False, True])
def test_time_product_reuses_checked_domain_with_direct_count(
    window_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    predicate: bool,
) -> None:
    import pandas as pd

    from marivo.analysis.errors import AnalysisError
    from marivo.datasource.adapters import CompiledRead, SourceSession

    session = window_session
    people = session.members(session.catalog.entities.get("sales.orders"))
    amount = session.catalog.fields.get("sales.orders.amount")
    scope = session.time_scope(start="2026-10-07", end="2026-10-09")
    frame = session.observe(
        {"value": amount.sum(), "people": people.count()},
        by=mv.product(scope, people),
        where=mv.in_population(people) if predicate else None,
    )
    reads: list[str] = []
    execute = SourceSession.execute

    def record(source: SourceSession, read: CompiledRead) -> pd.DataFrame:
        reads.append(read.purpose)
        return execute(source, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    rows = frame.compute().to_pandas()
    assert rows[("value", "value")].to_dict() == {0: None, 1: 20}
    assert rows[("value", "people")].tolist() == [1, 1]
    assert len([purpose for purpose in reads if purpose.startswith("query.")]) == 2
    with duckdb.connect(str(session.project_root / "windows.duckdb")) as connection:
        connection.execute(
            "INSERT INTO orders (id, customer, ordered_at, amount) VALUES (NULL, 7, '2026-09-20 00:00:00+00', 0)"
        )
    with pytest.raises(AnalysisError, match="non-null complete identity keys"):
        frame.compute()
