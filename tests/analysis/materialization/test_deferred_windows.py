"""Window endpoints reevaluate complete native formulas over actual source rows."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.binding.windows import _reset
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
            "CREATE TABLE orders (id BIGINT, customer BIGINT, ordered_at TIMESTAMPTZ, amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1, 7, '2026-09-20 00:00:00+00', 100), (2, 7, '2026-10-07 00:00:00+00', 20)"
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
customer_ref = ms.dimension_column(name="customer", entity=orders, column="customer")
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
    return mv.session.get_or_create("windows", report_timezone="UTC")


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
            during=scope.by(mv.grain("day")),
        )
        .execute()
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
                {"mtd": metric}, during=session.time_scope(start=start, end="2026-10-08")
            )
            .execute()
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
    assert endpoint - mv.trailing_duration(duration=timedelta(hours=24)).duration != start


@pytest.mark.parametrize("calendar_first", [False, True])
def test_calendar_population_axis_order(window_session: Session, calendar_first: bool) -> None:
    session = window_session
    revenue = session.catalog.metrics.get("sales.revenue")
    customer = session.catalog.fields.get("sales.orders.customer")
    customers = mv.coordinates(by=(customer,), keys=((7,), (8,)))
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    domain = mv.product(calendar, customers) if calendar_first else mv.product(customers, calendar)
    result = session.observe({"revenue": revenue}, by=domain).execute().to_pandas()
    assert result[("value", "revenue")].to_dict() == {0: 20, 1: None, 2: None, 3: None}
    assert len(result) == 4
    days = [datetime(2026, 10, 7, tzinfo=timezone.utc), datetime(2026, 10, 8, tzinfo=timezone.utc)]
    assert result.iloc[:, 0].tolist() == (
        [days[0], days[0], days[1], days[1]] if calendar_first else [7, 7, 8, 8]
    )
    assert result.iloc[:, 1].tolist() == ([7, 8, 7, 8] if calendar_first else days * 2)


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
        .execute()
        .to_pandas()
    )
    assert result[("value", "mean")].tolist() == [20.0]
    assert result[("value", "reverse_mean")].tolist() == [20.0]


def test_source_population_calendar_product(window_session: Session) -> None:
    session = window_session
    revenue = session.catalog.metrics.get("sales.revenue")
    customer = session.catalog.fields.get("sales.orders.customer")
    customers = session.members(ms.ref.entity("sales.orders")).classify(by=(customer,))
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    samples = mv.product(customers, calendar)
    result = session.observe({"revenue": revenue}, by=samples).execute().to_pandas()
    assert result[("value", "revenue")].to_dict() == {0: 20, 1: None}
    average = session.observe({"mean": revenue.per(samples).mean()}).execute().to_pandas()
    assert average[("value", "mean")].tolist() == [20.0]


def test_population_calendar_does_not_guess_window_endpoints(window_session: Session) -> None:
    from marivo.analysis.core.model import CoreRuleError

    session = window_session
    revenue = session.catalog.metrics.get("sales.revenue")
    customer = session.catalog.fields.get("sales.orders.customer")
    customers = mv.coordinates(by=(customer,), keys=((7,),))
    calendar = session.time_scope(start="2026-10-07", end="2026-10-09").by(mv.grain("day"))
    with pytest.raises(CoreRuleError, match="explicit during CalendarAxis"):
        session.observe(
            {"history": revenue.window(mv.all_history())}, by=mv.product(customers, calendar)
        )
