"""Window endpoints reevaluate complete native formulas over actual source rows."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
from marivo._definition.windows import TrailingDuration
from marivo.analysis.binding.windows import _reset
from marivo.analysis.core.data_schema import CalendarDataAxis
from marivo.analysis.core.frame_data import MemoryFrameBacking
from marivo.analysis.core.frozen_calendar import read_calendar_directory
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value


@pytest.fixture
def window_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Session:
    physical, parser = getattr(request, "param", ("TIMESTAMPTZ", "ms.timestamp()"))
    database = tmp_path / "windows.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("SET threads = 1")
        connection.execute(
            f"CREATE TABLE orders (id BIGINT, customer BIGINT, ordered_at {physical}, amount BIGINT, region VARCHAR, channel VARCHAR, encoded_at VARCHAR, date_at DATE, hour_at BIGINT)"
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
at_ref = ms.time_dimension_column(name="ordered_at", entity=orders, column="ordered_at", is_default=True)
sample_ref = ms.time_dimension_column(name="sample_at", entity=orders, column="ordered_at", parse=SAMPLING_PARSER, sample_interval=(1, "hour"))
sample2_ref = ms.time_dimension_column(name="sample_two", entity=orders, column="ordered_at", parse=SAMPLING_PARSER, sample_interval=(2, "hour"))
encoded_ref = ms.time_dimension_column(name="encoded_at", entity=orders, column="encoded_at", parse=ms.strptime("%Y%m%d%H", timezone="UTC"), sample_interval=(1, "hour"))
day_ref = ms.time_dimension_column(name="date_at", entity=orders, column="date_at")
hour_ref = ms.time_dimension_column(name="hour_at", entity=orders, column="hour_at", parse=ms.hour_prefix(day_ref), sample_interval=(1, "hour"))
customer_ref = ms.dimension_column(name="customer", entity=orders, column="customer")
region_ref = ms.dimension_column(name="region", entity=orders, column="region")
channel_ref = ms.dimension_column(name="channel", entity=orders, column="channel")
amount = amount_ref
customer = customer_ref
@ms.metric()
def revenue():
    return amount.sum()
""".replace("SAMPLING_PARSER", parser),
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("windows", timezone="UTC")


def _weekly_facts(tmp_path: Path) -> None:
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders (id, customer, ordered_at, amount, region, channel) VALUES "
            "(1,6,'2020-12-15 00:00:00+00',5,'old','web'),"
            "(2,7,'2020-12-22 00:00:00+00',10,'north','web'),"
            "(3,8,'2020-12-23 00:00:00+00',30,'south','shop'),"
            "(4,7,'2020-12-29 00:00:00+00',200,'gap','only'),"
            "(5,7,'2021-01-05 00:00:00+00',20,'north','web'),"
            "(6,7,'2021-01-06 00:00:00+00',30,'north','web'),"
            "(7,9,'2021-01-07 00:00:00+00',100,'new','shop')"
        )


@pytest.mark.parametrize(
    "window_session",
    [
        ("TIMESTAMPTZ", "ms.timestamp()"),
        ("TIMESTAMP", "ms.timestamp(timezone='UTC')"),
        ("TIMESTAMP", "ms.timestamp()"),
    ],
    indirect=True,
)
def test_sampling_uses_source_clock_across_report_zones(
    window_session: Session, tmp_path: Path
) -> None:
    session = window_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    sampled = catalog_value(session.catalog, "sales.orders.sample_at", kind="field")
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders (id, ordered_at, amount) VALUES (1, '2026-01-15 00:00:00+00', 10), (2, '2026-01-15 01:00:00+00', 20), (3, '2026-01-15 02:00:00+00', 30)"
        )
    metric = amount.sum(time=sampled).per(sampled).median()
    for zone in ("UTC", "Asia/Kolkata"):
        scope = session.time_scope(
            start="2026-01-15T00:00Z", end="2026-01-15T03:00Z", timezone=zone
        )
        assert session.observe({"mean": metric}, by=(scope,)).compute().to_pandas()[
            "mean"
        ].tolist() == [20.0]
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id=2")
    for zone in ("UTC", "Asia/Kolkata"):
        scope = session.time_scope(
            start="2026-01-15T00:00Z", end="2026-01-15T03:00Z", timezone=zone
        )
        assert session.observe({"mean": metric}, by=(scope,)).compute().to_pandas()[
            "mean"
        ].tolist() == [20.0]


@pytest.mark.parametrize("month", [1, 7])
def test_aware_two_hour_sampling_keeps_utc_phase(
    window_session: Session, tmp_path: Path, month: int
) -> None:
    session = window_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    sampled = catalog_value(session.catalog, "sales.orders.sample_two", kind="field")
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders (id, ordered_at, amount) VALUES (?, ?, ?)",
            [
                (index, f"2026-{month:02d}-15 {hour:02d}:00:00+00", 10 * (index + 1))
                for index, hour in enumerate((0, 2, 4))
            ],
        )
    metric = amount.sum(time=sampled).per(sampled).median()
    for zone in ("UTC", "Europe/Berlin"):
        scope = session.time_scope(
            start=f"2026-{month:02d}-15T00:00Z", end=f"2026-{month:02d}-15T06:00Z", timezone=zone
        )
        assert session.observe({"mean": metric}, by=(scope,)).compute().to_pandas()[
            "mean"
        ].tolist() == [20.0]
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id=1")
    for zone in ("UTC", "Europe/Berlin"):
        scope = session.time_scope(
            start=f"2026-{month:02d}-15T00:00Z", end=f"2026-{month:02d}-15T06:00Z", timezone=zone
        )
        assert session.observe({"mean": metric}, by=(scope,)).compute().to_pandas()[
            "mean"
        ].tolist() == [20.0]


def test_sampling_encoding_null_and_duplicate_fact_contract(
    window_session: Session, tmp_path: Path
) -> None:
    session = window_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    fields = [
        catalog_value(session.catalog, f"sales.orders.{name}", kind="field")
        for name in ("sample_at", "encoded_at", "hour_at")
    ]
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders (id, ordered_at, amount, encoded_at, date_at, hour_at) VALUES (1,'2026-01-15 00:00:00+00',4,'2026011500','2026-01-15',0), (2,'2026-01-15 00:00:00+00',6,'2026011500','2026-01-15',0), (3,'2026-01-15 01:00:00+00',NULL,'2026011501','2026-01-15',1), (4,'2026-01-15 02:00:00+00',30,'2026011502','2026-01-15',2)"
        )
    scope = session.time_scope(
        start="2026-01-15T00:00Z", end="2026-01-15T03:00Z", timezone="Asia/Kolkata"
    )
    for field in fields:
        values = (
            session.observe({"mean": amount.sum(time=field).per(field).median()}, by=(scope,))
            .compute()
            .to_pandas()
        )
        assert values["mean"].tolist() == [20.0]
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id=3")
    for field in fields:
        rows = (
            session.observe({"median": amount.sum(time=field).per(field).median()}, by=(scope,))
            .compute()
            .to_pandas()
        )
        assert rows["median"].tolist() == [20.0]
    unsampled = catalog_value(session.catalog, "sales.orders.ordered_at", kind="field")
    assert session.observe(
        {"mean": amount.sum(time=unsampled).per(unsampled).mean()}, by=(scope,)
    ).compute().to_pandas()["mean"].tolist() == [20.0]


@pytest.mark.parametrize("window_session", [("DATE", "None")], indirect=True)
def test_date_contributions_reject_subday_and_non_midnight_windows(
    window_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = window_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    scope = session.time_scope(start="2026-10-07", end="2026-10-08")
    assert session.observe(
        {"revenue": amount.sum()}, by=(scope.by(mv.grain("day")),)
    ).compute().to_pandas()["revenue"].tolist() == [20]
    at = catalog_value(session.catalog, "sales.orders.ordered_at", kind="field")
    assert session.observe({"revenue": amount.sum()}, where=at.within(scope)).compute().to_pandas()[
        "revenue"
    ].tolist() == [20]
    from marivo.datasource.adapters import SourceSession

    def fail_read(*args: object, **kwargs: object) -> None:
        pytest.fail("invalid DATE contribution reached a business read")

    monkeypatch.setattr(SourceSession, "execute", fail_read)
    for metric, axis in (
        (amount.sum(), scope.by(mv.grain("hour"))),
        (amount.sum().window(mv.to_date(grain=mv.grain("hour"))), scope),
        (amount.sum().window(mv.trailing_duration(duration=timedelta(hours=6))), scope),
        (amount.sum().window(mv.trailing_duration(duration=timedelta(hours=25))), scope),
        (amount.sum(), session.time_scope(start="2026-10-07", end="2026-10-07T12:00Z")),
    ):
        with pytest.raises(AnalysisError, match=r"DATE|midnight"):
            session.observe({"revenue": metric}, by=(axis,)).compute()
    for start, end in (("2026-03-08", "2026-03-09"), ("2026-11-01", "2026-11-02")):
        dst_scope = session.time_scope(start=start, end=end, timezone="America/New_York")
        with pytest.raises(AnalysisError, match=r"DATE|midnight"):
            session.observe(
                {"bad": amount.sum().window(mv.trailing_duration(duration=timedelta(hours=24)))},
                by=(dst_scope,),
            ).compute()


@pytest.mark.parametrize(
    "window_session", [("TIMESTAMP", "ms.timestamp(timezone='America/New_York')")], indirect=True
)
def test_naive_source_sampling_uses_representable_wall_clocks(
    window_session: Session, tmp_path: Path
) -> None:
    session = window_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    sample = catalog_value(session.catalog, "sales.orders.sample_at", kind="field")
    metric = amount.sum(time=sample).per(sample).median()
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders (id, ordered_at, amount) VALUES (1,'2026-03-08 00:00:00',10), (2,'2026-03-08 01:00:00',20), (3,'2026-03-08 03:00:00',30)"
        )
    scope = session.time_scope(start="2026-03-08T05:00Z", end="2026-03-08T08:00Z")
    assert session.observe({"mean": metric}, by=(scope,)).compute().to_pandas()[
        "mean"
    ].tolist() == [20.0]
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders (id, ordered_at, amount) VALUES (1,'2026-11-01 00:00:00',10), (2,'2026-11-01 01:00:00',20), (3,'2026-11-01 02:00:00',30)"
        )
    scope = session.time_scope(start="2026-11-01T04:00Z", end="2026-11-01T08:00Z")
    assert session.observe({"mean": metric}, by=(scope,)).compute().to_pandas()[
        "mean"
    ].tolist() == [20.0]
    assert session.observe({"mean": amount.mean(time=sample)}, by=(scope,)).compute().to_pandas()[
        "mean"
    ].tolist() == [20.0]
    first_fold = session.time_scope(start="2026-11-01T05:00Z", end="2026-11-01T06:00Z")
    for selected in (first_fold, first_fold.by(mv.grain("hour"))):
        result = (
            session.observe({"mean": amount.mean(time=sample)}, by=(selected,))
            .compute()
            .to_pandas()
        )
        assert result["mean"].isna().all()
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders (id, ordered_at, amount) VALUES (1,'2026-03-08 02:30:00',10)"
        )
    scope = session.time_scope(start="2026-03-08T05:00Z", end="2026-03-08T08:00Z")
    assert session.observe({"mean": amount.mean(time=sample)}, by=(scope,)).compute().to_pandas()[
        "mean"
    ].tolist() == [10.0]

    # DuckDB projects skipped 02:00 to the same instant as 03:00. That raw
    # skipped clock remains a represented sample without clock completion.
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders (id, ordered_at, amount) VALUES "
            "(1,'2026-03-08 00:00:00',10), (2,'2026-03-08 01:00:00',20), "
            "(3,'2026-03-08 02:00:00',30)"
        )
    rows = session.observe({"median": metric}, by=(scope,)).compute().to_pandas()
    assert rows["median"].tolist() == [20.0]


@pytest.mark.parametrize(
    "window_session", [("TIMESTAMP", "ms.timestamp(timezone='America/New_York')")], indirect=True
)
@pytest.mark.parametrize(
    "start,end,clocks,expected,clipped,selected_ids",
    [
        (
            "2026-11-01T04:00Z",
            "2026-11-01T08:00Z",
            (
                "2026-11-01 00:45",
                "2026-11-01 01:00",
                "2026-11-01 01:15",
                "2026-11-01 01:45",
                "2026-11-01 02:15",
            ),
            [1, 0, 14, 16],
            ("2026-11-01T05:30Z", "2026-11-01T06:30Z"),
            [2, 3],
        ),
        (
            "2026-03-08T06:00Z",
            "2026-03-08T08:00Z",
            (
                "2026-03-08 01:15",
                "2026-03-08 02:00",
                "2026-03-08 02:30",
                "2026-03-08 03:15",
                "2026-03-08 04:00",
            ),
            [1, 14],
            ("2026-03-08T07:00Z", "2026-03-08T07:30Z"),
            [2, 3, 4],
        ),
    ],
)
def test_wall_membership_calendar_windows_and_population_agree(
    window_session: Session,
    tmp_path: Path,
    start: str,
    end: str,
    clocks: tuple[str, ...],
    expected: list[int],
    clipped: tuple[str, str],
    selected_ids: list[int],
) -> None:
    session = window_session
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders (id, ordered_at, amount) VALUES (?, ?, ?)",
            [(index + 1, clock, 2**index) for index, clock in enumerate(clocks)],
        )
        connection.execute("INSERT INTO orders (id, amount) VALUES (99, 999)")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    at = catalog_value(session.catalog, "sales.orders.sample_at", kind="field")
    metric = amount.sum(time=at)
    scope = session.time_scope(start=start, end=end)
    calendar = scope.by(mv.grain("hour"))
    values = (
        session.observe(
            {"ordinary": metric, "history": metric.window(mv.all_history())}, by=(calendar,)
        )
        .compute()
        .to_pandas()
    )
    assert values.attrs["frozen_coordinates"][0]["rows"]["ordinal"].tolist() == list(
        range(len(expected))
    )
    assert values["ordinary"].tolist() == expected
    assert values["history"].tolist() == [
        sum(expected[: index + 1]) for index in range(len(expected))
    ]
    selected_scope = session.time_scope(start=clipped[0], end=clipped[1])
    members = session.members(
        session.catalog.get("entity:sales.orders").value, where=at.within(selected_scope)
    )
    assert session.frame(members).compute().to_pandas().iloc[:, 0].tolist() == selected_ids
    total = sum(2 ** (index - 1) for index in selected_ids)
    assert session.observe({"value": metric}, by=(selected_scope,)).compute().to_pandas()[
        "value"
    ].tolist() == [total]
    # Membership owns its clipped selection even when contribution time expands.
    assert session.observe(
        {"value": metric}, by=(scope,), where=mv.in_population(members)
    ).compute().to_pandas()["value"].tolist() == [total]


def test_sparse_shift_observes_raw_contributions_and_nominal_windows(
    window_session: Session, tmp_path: Path
) -> None:
    _weekly_facts(tmp_path)
    session = window_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
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
    observed = {
        **metrics,
        **{
            f"trailing_{name}": metric.window(mv.trailing_periods(count=2))
            for name, metric in metrics.items()
        },
    }
    ordinary = session.observe(observed, by=(calendar,)).compute()
    values = ordinary.to_pandas(time_details=True)
    assert values["calendar:week.label"].tolist() == ["2020-12-21", "2021-01-04"]
    assert values["revenue"].tolist() == [40, 150]
    assert values["aov"].tolist() == [20.0, 50.0]
    assert values["customers"].tolist() == [2, 2]
    assert values["median"].tolist() == [20.0, 30.0]
    assert values["trailing_revenue"].tolist() == [45, 350]
    assert values["trailing_aov"].tolist() == [15.0, 87.5]
    assert values["trailing_customers"].tolist() == [3, 2]
    assert values["trailing_median"].tolist() == [10.0, 65.0]
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
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    calendar = (
        session.time_scope(start="2020-12-28", end="2021-01-04")
        .by(mv.grain("week"))
        .shift(mv.offset(years=-1))
    )
    metric = amount.count() / (amount.count() * 0)
    if windowed:
        metric = metric.window(mv.trailing_periods(count=2))
    sampled = catalog_value(session.catalog, "sales.orders.sample_at", kind="field")
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
    assert session.observe({"bad": amount.count() / 0}).compute().to_pandas()["bad"].isna().all()


def test_sparse_calendar_joint_groups_and_fixed_populations(
    window_session: Session, tmp_path: Path
) -> None:
    _weekly_facts(tmp_path)
    session = window_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    region = catalog_value(session.catalog, "sales.orders.region", kind="field")
    channel = catalog_value(session.catalog, "sales.orders.channel", kind="field")
    calendar = (
        session.time_scope(start="2019-12-23", end="2020-01-06")
        .by(mv.grain("week"))
        .shift(mv.offset(years=1))
    )
    strict = amount.sum() / (amount.count() - 1)
    assert session.observe({"strict": strict}, by=(calendar,)).compute().to_pandas(
        time_details=True
    )["strict"].tolist() == [40.0, 75.0]
    rows = (
        session.observe({"total": amount.sum()}, by=(calendar, region, channel))
        .compute()
        .to_pandas(time_details=True)
    )
    assert set(rows[["region", "channel"]].itertuples(index=False, name=None)) == {
        ("north", "web"),
        ("south", "shop"),
        ("new", "shop"),
    }
    assert len(rows) == 6
    gap = session.time_scope(start="2020-12-28", end="2021-01-04").by(mv.grain("week"))
    assert (
        session.observe({"strict": strict}, by=(gap,))
        .compute()
        .to_pandas(time_details=True)["strict"]
        .isna()
        .all()
    )
    pairs = mv.coordinates(by=(region, channel), keys=(("north", "web"), ("south", "shop")))
    explicit = (
        session.observe(
            {"total": amount.sum()}, by=mv.product(calendar, pairs), where=mv.in_population(pairs)
        )
        .compute()
        .to_pandas(time_details=True)
    )
    assert explicit[["calendar:week.label", "region", "channel"]].to_records(
        index=False
    ).tolist() == [
        ("2020-12-21", "north", "web"),
        ("2020-12-21", "south", "shop"),
        ("2021-01-04", "north", "web"),
        ("2021-01-04", "south", "shop"),
    ]
    assert explicit["total"].to_dict() == {0: 10, 1: 30, 2: 50, 3: 0}
    orders = session.catalog.get("entity:sales.orders").value
    selected = mv.coordinates(by=(mv.identity(orders),), keys=(((2,),), ((5,),)))
    filtered = (
        session.observe(
            {"total": amount.sum(), "history": amount.sum().window(mv.trailing_periods(count=2))},
            by=(calendar,),
            where=mv.in_population(selected),
        )
        .compute()
        .to_pandas(time_details=True)
    )
    assert filtered["total"].tolist() == [10, 20]
    assert filtered["history"].tolist() == [10, 20]
    at = catalog_value(session.catalog, "sales.orders.ordered_at", kind="field")
    hard = (
        session.observe(
            {"history": amount.sum().window(mv.trailing_periods(count=2))},
            by=(calendar,),
            where=at >= datetime(2021, 1, 1, tzinfo=timezone.utc),
        )
        .compute()
        .to_pandas(time_details=True)
    )
    assert hard["history"].to_dict() == {0: 0, 1: 150}


def test_window_recomputes_history_ratio_and_distinct(
    window_session: Session, tmp_path: Path
) -> None:
    session = window_session
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders (id, customer, ordered_at, amount) VALUES (1,7,'2026-09-20 00:00:00+00',100), (2,7,'2026-10-07 00:00:00+00',10), (3,7,'2026-10-07 12:00:00+00',30), (4,7,'2026-10-08 00:00:00+00',60)"
        )
    revenue = session.catalog.get("metric:sales.revenue").value
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    scope = session.time_scope(start="2026-10-07", end="2026-10-09")
    values = (
        session.observe(
            {
                "revenue": revenue.window(mv.all_history()),
                "aov": (revenue / amount.count()).window(mv.all_history()),
                "customers": customer.count_distinct().window(mv.all_history()),
                "mean": amount.mean().window(mv.all_history()),
                "median": amount.quantile(q=0.5).window(mv.all_history()),
                "filtered": revenue.where(amount >= 20).window(mv.all_history()),
            },
            by=(scope.by(mv.grain("day")),),
        )
        .compute()
        .to_pandas()
    )
    assert values["revenue"].tolist() == [140, 200]
    assert values["aov"].tolist() == pytest.approx([140 / 3, 50])
    assert values["mean"].tolist() == pytest.approx([140 / 3, 50])
    assert values["median"].tolist() == [30.0, 45.0]
    assert values["filtered"].tolist() == [130, 190]
    assert values["customers"].tolist() == [1, 1]
    daily = (
        session.observe({"daily": customer.count_distinct()}, by=(scope.by(mv.grain("day")),))
        .compute()
        .to_pandas()
    )
    assert daily["daily"].tolist() == [1, 1]
    assert sum(daily["daily"]) == 2


def test_scalar_to_date_uses_endpoint_not_display_start(window_session: Session) -> None:
    session = window_session
    revenue = session.catalog.get("metric:sales.revenue").value
    metric = revenue.window(mv.to_date(grain=mv.grain("month")))
    for start in ("2026-09-01", "2026-10-01"):
        result = (
            session.observe(
                {"mtd": metric}, by=(session.time_scope(start=start, end="2026-10-08"),)
            )
            .compute()
            .to_pandas()
        )
        assert result["mtd"].tolist() == [20]


def test_partial_month_windows_stop_at_actual_endpoint(
    window_session: Session, tmp_path: Path
) -> None:
    session = window_session
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders (id, ordered_at, amount) VALUES (1, '2026-10-01 00:00:00+00', 1), (2, '2026-10-14 11:00:00+00', 10), (3, '2026-10-14 12:00:00+00', 20), (4, '2026-10-15 11:00:00+00', 30), (5, '2026-10-15 12:00:00+00', 1000), (6, '2026-10-31 00:00:00+00', 10000)"
        )
    revenue = session.catalog.get("metric:sales.revenue").value
    scope = session.time_scope(start="2026-10-01", end="2026-10-15T12:00Z")
    values = (
        session.observe(
            {
                "mtd": revenue.window(mv.to_date(grain=mv.grain("month"))),
                "elapsed": revenue.window(mv.trailing_duration(duration=timedelta(hours=24))),
                "since": revenue.window(mv.since(start=session.instant("2026-10-14T11:00Z"))),
            },
            by=(scope.by(mv.grain("month")),),
        )
        .compute()
        .to_pandas()
    )
    assert values["mtd"].tolist() == [61]
    assert values["elapsed"].tolist() == [50]
    assert values["since"].tolist() == [60]
    for partial in (scope, session.time_scope(start="2026-10-02", end="2026-11-01")):
        with pytest.raises(AnalysisError, match="complete calendar buckets"):
            session.observe(
                {"bad": revenue.window(mv.trailing_periods(count=1))},
                by=(partial.by(mv.grain("month")),),
            )


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
    for hour in (6, 7):
        endpoint = datetime(2026, 11, 1, hour, tzinfo=timezone.utc)
        assert _reset(endpoint, mv.grain("hour"), "America/New_York") == (
            datetime(2026, 11, 1, hour - 1, tzinfo=timezone.utc),
            endpoint,
        )


def test_hour_reset_preserves_both_aware_fold_buckets(
    window_session: Session, tmp_path: Path
) -> None:
    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders (id, ordered_at, amount) VALUES (1,'2026-11-01 05:30:00+00',10), (2,'2026-11-01 06:30:00+00',30)"
        )
    revenue = window_session.catalog.get("metric:sales.revenue").value
    hours = window_session.time_scope(
        start="2026-11-01T04:00Z", end="2026-11-01T07:00Z", timezone="America/New_York"
    ).by(mv.grain("hour"))
    values = (
        window_session.observe(
            {"hourly": revenue.window(mv.to_date(grain=mv.grain("hour")))}, by=(hours,)
        )
        .compute()
        .to_pandas()
    )
    assert values["hourly"].tolist() == [0, 10, 30]
    assert values["hourly"].iloc[1:].tolist() == [10, 30]


@pytest.mark.parametrize("calendar_first", [False, True])
def test_calendar_population_axis_order(window_session: Session, calendar_first: bool) -> None:
    session = window_session
    revenue = session.catalog.get("metric:sales.revenue").value
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
    customers = mv.coordinates(by=(customer,), keys=((7,), (8,)))
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    domain = mv.product(calendar, customers) if calendar_first else mv.product(customers, calendar)
    completed = session.observe({"revenue": revenue}, by=domain).compute()
    result = completed.to_pandas(time_details=True)
    assert result["revenue"].to_dict() == {0: 20, 1: 0, 2: 0, 3: 0}
    assert len(result) == 4
    days = [datetime(2026, 10, 7, tzinfo=timezone.utc), datetime(2026, 10, 8, tzinfo=timezone.utc)]
    assert result["calendar:day.label"].tolist() == (
        ["2026-10-07"] * 2 + ["2026-10-08"] * 2
        if calendar_first
        else ["2026-10-07", "2026-10-08"] * 2
    )
    assert result["customer"].tolist() == ([7, 8, 7, 8] if calendar_first else [7, 7, 8, 8])
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
    revenue = session.catalog.get("metric:sales.revenue").value
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
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
    assert result["mean"].tolist() == [5.0]
    assert result["reverse_mean"].tolist() == [5.0]


def test_source_population_calendar_product(window_session: Session) -> None:
    session = window_session
    revenue = session.catalog.get("metric:sales.revenue").value
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
    customers = session.members(session.catalog.get("entity:sales.orders").value).classify(
        by=(customer,)
    )
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    samples = mv.product(customers, calendar)
    result = session.observe({"revenue": revenue}, by=samples).compute().to_pandas()
    assert result["revenue"].to_dict() == {0: 20, 1: 0}
    average = session.observe({"mean": revenue.per(samples).mean()}).compute().to_pandas()
    assert average["mean"].tolist() == [10.0]


def test_population_calendar_supplies_explicit_window_endpoints(window_session: Session) -> None:
    session = window_session
    revenue = session.catalog.get("metric:sales.revenue").value
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
    customers = mv.coordinates(by=(customer,), keys=((7,),))
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    result = (
        session.observe(
            {"history": revenue.window(mv.all_history())}, by=mv.product(customers, calendar)
        )
        .compute()
        .to_pandas()
    )
    assert result["history"].tolist() == [120, 120]


def test_interval_selection_keeps_window_history_and_hard_fact_filter(
    window_session: Session,
) -> None:
    session = window_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    at = catalog_value(session.catalog, "sales.orders.ordered_at", kind="field")
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
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
    assert rows["ordinary"].to_dict() == {0: 20, 1: 0}
    assert rows["history"].to_dict() == {0: 120, 1: 0}
    restricted = (
        session.observe(
            {"history": amount.sum().window(mv.all_history())},
            by=(scope,),
            where=at >= session.instant("2026-10-01"),
        )
        .compute()
        .to_pandas()
    )
    assert restricted["history"].tolist() == [20]


def test_explicit_count_repeats_time_and_rejects_ineffective_window(
    window_session: Session,
) -> None:
    from marivo.analysis.errors import AnalysisError

    session = window_session
    entity = session.catalog.get("entity:sales.orders").value
    people = mv.coordinates(by=(mv.identity(entity),), keys=(((1,),), ((2,),), ((999,),)))
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    rows = session.observe({"people": people.count()}, by=(calendar,)).compute().to_pandas()
    assert rows["people"].tolist() == [3, 3]
    with pytest.raises(AnalysisError, match="fact contributions for a window"):
        session.observe({"people": people.count().window(mv.all_history())}, by=(calendar,))


def test_interval_internal_sample_domain_keeps_its_own_time(window_session: Session) -> None:
    session = window_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    customer = catalog_value(session.catalog, "sales.orders.customer", kind="field")
    scope = session.time_scope(start="2026-10-07", end="2026-10-09")
    customers = mv.coordinates(by=(customer,), keys=((7,), (8,)))
    rows = (
        session.observe({"mean": amount.sum().per(mv.product(scope, customers)).mean()})
        .compute()
        .to_pandas()
    )
    assert rows["mean"].tolist() == [10.0]
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
    people = session.members(session.catalog.get("entity:sales.orders").value)
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
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
    assert rows["value"].to_dict() == {0: 0, 1: 20}
    assert rows["people"].tolist() == [1, 1]
    assert len([purpose for purpose in reads if purpose.startswith("query.")]) == 2
    with duckdb.connect(str(session.project_root / "windows.duckdb")) as connection:
        connection.execute(
            "INSERT INTO orders (id, customer, ordered_at, amount) VALUES (NULL, 7, '2026-09-20 00:00:00+00', 0)"
        )
    with pytest.raises(AnalysisError, match="non-null complete identity keys"):
        frame.compute()


@pytest.mark.parametrize("native", [False, True])
def test_sparse_sample_reductions_keep_groups_and_offgrid_clocks(
    window_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    native: bool,
) -> None:
    from tests.support.source_trace import capture_source

    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.executemany(
            "INSERT INTO orders(id, ordered_at, amount, region) VALUES (?, ?, ?, ?)",
            [
                (group * 2 + index, f"2026-01-15 {clock}+00", 10, f"cluster-{group}")
                for group in range(8)
                for index, clock in enumerate(("00:00:01", "02:00:00"))
            ],
        )
    session = window_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    point = catalog_value(session.catalog, "sales.orders.sample_at", kind="field")
    region = catalog_value(session.catalog, "sales.orders.region", kind="field")
    scope = session.time_scope(start="2026-01-15T00:00Z", end="2026-01-15T03:00Z")
    sample = amount.cast("float64").mean(time=point).per(point)
    trace = capture_source(monkeypatch)
    assert (
        session.observe({"average": sample.mean()}, by=(scope, region))
        .compute()
        .to_pandas()["average"]
        .tolist()
        == [10.0] * 8
    )
    rows = (
        session.observe(
            {"peak": sample.max() if native else sample.quantile(0.95)}, by=(scope, region)
        )
        .compute()
        .to_pandas()
    )
    assert rows["peak"].tolist() == [10.0] * 8
    assert len(trace.returned_rows) == 2
    if native:
        assert trace.returned_rows == [8, 8]


def test_nested_sparse_sample_layers_keep_conditional_demand(
    window_session: Session,
    tmp_path: Path,
) -> None:
    import ibis

    with duckdb.connect(str(tmp_path / "windows.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders(id,ordered_at,amount,region) VALUES (1,'2026-01-15 00:00:00+00',10,'A'),(2,'2026-01-15 02:00:00+00',30,'A')"
        )
    session = window_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    point = catalog_value(session.catalog, "sales.orders.sample_at", kind="field")
    region = catalog_value(session.catalog, "sales.orders.region", kind="field")
    scope = session.time_scope(start="2026-01-15T00:00Z", end="2026-01-15T03:00Z")
    strict = amount.sum(time=point).per(point).quantile(0.95)
    nested = strict.per(region).mean()
    assert session.observe({"nested": nested}, by=(scope,)).compute().to_pandas()[
        "nested"
    ].tolist() == [29.0]
    guarded = ibis.ifelse(amount.sum(time=point) < 0, nested, 0)
    assert session.observe({"guarded": guarded}, by=(scope,)).compute().to_pandas()[
        "guarded"
    ].tolist() == [0.0]
    rows = (
        session.observe(
            {"guarded": ibis.ifelse(amount.sum(time=point) > 0, nested, 0)}, by=(scope,)
        )
        .compute()
        .to_pandas()
    )
    assert rows["guarded"].tolist() == [29.0]
    # A bounded window changes the consumed interval, not the mean's sample policy.
    mean = (
        amount.sum(time=point)
        .per(point)
        .mean()
        .window(mv.trailing_duration(duration=timedelta(hours=3)))
    )
    assert session.observe({"mean": mean}, by=(scope,)).compute().to_pandas()["mean"].tolist() == [
        20.0
    ]
    source = amount.cast("float64").mean(time=point)
    plain = source.per(point).mean()
    formulas = (
        session.observe(
            {
                "arithmetic": plain * 2 + plain,
                "conditional": ibis.ifelse(amount.sum(time=point) > 0, plain, 0),
            },
            by=(scope,),
        )
        .compute()
        .to_pandas()
    )
    assert formulas["arithmetic"].tolist() == [60.0]
    assert formulas["conditional"].tolist() == [20.0]
    filtered = source.where(amount > 10).per(point)
    for metric in (filtered.max(), filtered.quantile(0.95)):
        rows = (
            session.observe({"average": plain, "filtered": metric}, by=(scope,))
            .compute()
            .to_pandas()
        )
        assert rows["average"].tolist() == [20.0]
        assert rows["filtered"].tolist() == [30.0]
