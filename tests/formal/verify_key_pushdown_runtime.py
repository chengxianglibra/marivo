"""Exercise the admitted ordinary key restriction on each governed SQL provider."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from sqlglot import exp, parse_one

import marivo.analysis as mv
from marivo.analysis.model.execution_errors import MaterializationError
from marivo.analysis.model.optimization_policy import OptimizationPolicy
from marivo.datasource.spi import TableSourceIR
from marivo.semantic.reader import SemanticProject
from tests.analysis.materialization.test_key_predicate_pushdown import _OFF, _decisions
from tests.analysis.numeric.test_remote_numeric_query import CASES, _datasource_model
from tests.datasource.source_cases import SourceData, source_case, source_params
from tests.shared_fixtures import catalog_value
from tests.support.optimization import compute_with_policy
from tests.support.source_trace import capture_source

pytestmark = pytest.mark.runtime

_ROWS = (
    {"id": 1, "bucket": 7, "detail": 10, "amount": 25},
    {"id": 2, "bucket": 7, "detail": 10, "amount": 11},
    {"id": 3, "bucket": 7, "detail": 20, "amount": None},
    {"id": 4, "bucket": 9, "detail": 10, "amount": 19},
    {"id": 5, "bucket": 9, "detail": 20, "amount": -1},
)


@pytest.mark.parametrize("explicit", (False, True))
@pytest.mark.parametrize("fix", (False, True))
@pytest.mark.parametrize("provider,profile", source_params(CASES))
def test_live_ordinary_key_pushdown_preserves_native_aggregate(
    provider: str,
    profile: str,
    fix: bool,
    explicit: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    data = SourceData(
        "id BIGINT NOT NULL, bucket BIGINT NOT NULL, detail BIGINT NOT NULL, amount BIGINT",
        "(1,7,10,25),(2,7,10,11),(3,7,20,NULL),(4,9,10,19),(5,9,20,-1)",
        "id Int64, bucket Int64, detail Int64, amount Nullable(Int64)",
        [dict(item) for item in _ROWS],
    )
    monkeypatch.chdir(tmp_path)
    with source_case(provider, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(provider, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Tests', default=True)",
                "sales/model.py": f"""import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])
bucket = ms.dimension_column(name='bucket', entity=orders, column='bucket')
detail = ms.dimension_column(name='detail', entity=orders, column='detail')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())
""",
            }
        )
        session = mv.session.get_or_create("ordinary-pushdown", timezone="UTC")
        bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="dimension")
        detail = catalog_value(session.catalog, "sales.orders.detail", kind="dimension")
        amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
        domain = mv.coordinates(
            by=(bucket, detail), keys=((7, 10), (7, 20), (7, 30), (9, 10), (9, 20))
        )
        observed = session.observe(
            {"value": amount.min()}, by=domain if explicit else (bucket, detail)
        )
        selected = (
            observed.fix(observed.axes[0], 7)
            if fix
            else observed.where(observed.coord(observed.axes[0]) == 7)
        )
        trace = capture_source(monkeypatch)
        policy = OptimizationPolicy.default().with_rule("frame_terminal_fix", enabled=False)
        control = policy.with_rule("L7_key_pushdown", enabled=False)
        baseline = compute_with_policy(selected, control)
        assert trace.returned_rows == [4]
        trace.native_sql.clear()
        trace.returned_rows.clear()
        actual = compute_with_policy(selected, policy)
        pd.testing.assert_frame_equal(baseline.to_pandas(), actual.to_pandas())
        keys = [(7, 10, 11), (7, 20, None)]
        if explicit:
            keys.append((7, 30, None))
        expected = [row[1:] for row in keys] if fix else keys
        rows = [
            tuple(None if pd.isna(cell) else cell for cell in row)
            for row in actual.to_pandas().itertuples(index=False, name=None)
        ]
        assert len(rows) == len(expected) and set(rows) == set(expected)
        decisions = _decisions(selected, policy=policy)
        assert any(item.status == "adopted" for item in decisions), decisions
        assert trace.returned_rows == [2]
        assert len(trace.native_sql) == 1
        sql = parse_one(trace.native_sql[0], read=provider)
        assert any(
            group.parent is not None and group.parent.find(exp.Where) is not None
            for group in sql.find_all(exp.Group)
        )
        pd.testing.assert_frame_equal(selected.compute().to_pandas(), actual.to_pandas())


@pytest.mark.parametrize("window", (False, True), ids=("calendar-day", "trailing-window"))
@pytest.mark.parametrize("provider,profile", source_params(CASES))
def test_live_calendar_crops_ordinary_support_and_keeps_its_independent_branch(
    provider: str,
    profile: str,
    window: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    rows = [
        {"id": 1, "bucket": 7, "day": date(2025, 12, 31), "amount": 5},
        {"id": 2, "bucket": 7, "day": date(2026, 1, 1), "amount": 11},
        {"id": 3, "bucket": 7, "day": date(2026, 1, 2), "amount": None},
        {"id": 4, "bucket": 9, "day": date(2026, 1, 1), "amount": 19},
        {"id": 5, "bucket": 9, "day": date(2026, 1, 2), "amount": -1},
    ]
    literal = "DATE " if provider == "trino" else ""
    data = SourceData(
        "id BIGINT NOT NULL, bucket BIGINT NOT NULL, day DATE NOT NULL, amount BIGINT",
        (
            f"(1,7,{literal}'2025-12-31',5),(2,7,{literal}'2026-01-01',11),"
            f"(3,7,{literal}'2026-01-02',NULL),(4,9,{literal}'2026-01-01',19),"
            f"(5,9,{literal}'2026-01-02',-1)"
        ),
        "id Int64, bucket Int64, day Date, amount Nullable(Int64)",
        rows,
    )
    monkeypatch.chdir(tmp_path)
    with source_case(provider, profile, tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model(provider, case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Tests', default=True)",
                "sales/model.py": f"""import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])
bucket = ms.dimension_column(name='bucket', entity=orders, column='bucket')
day = ms.time_dimension_column(name='day', entity=orders, column='day')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())
""",
            }
        )
        session = mv.session.get_or_create("window-key-pushdown", timezone="UTC")
        bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="dimension")
        day = catalog_value(session.catalog, "sales.orders.day", kind="time_dimension")
        amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
        report = session.time_scope(start="2026-01-01", end="2026-01-04").by(mv.grain("day"))
        clocked = amount.min(time=day)
        if window:
            clocked = clocked.window(mv.trailing_periods(count=2))
        frame = session.observe(
            {"clocked": clocked, "independent": amount.min(time=None)}, by=(bucket, report)
        )
        query = frame.where(frame.coord(frame.axes[0]) == 7)
        trace = capture_source(monkeypatch)
        # Hold the independent window fusion off on both sides to isolate L7.
        policy = OptimizationPolicy.default().with_rule("l6_window_prereduction", enabled=False)
        control = policy.with_rule("L7_key_pushdown", enabled=False)
        baseline = compute_with_policy(query, control).to_pandas()
        baseline_rows = sum(trace.returned_rows)
        trace.returned_rows.clear()
        trace.native_sql.clear()
        actual = compute_with_policy(query, policy).to_pandas()
        pd.testing.assert_frame_equal(actual, baseline)
        expected = [5, 11, None] if window else [11, None]
        assert actual.iloc[:, 0].tolist() == [7] * len(expected)
        assert [None if pd.isna(cell) else cell for cell in actual["clocked"]] == expected
        assert actual["independent"].tolist() == [5] * len(expected)
        assert sum(trace.returned_rows) < baseline_rows
        decisions = _decisions(query, policy=policy)
        assert len(decisions) == 1
        assert decisions[0].status == "adopted", decisions
        pd.testing.assert_frame_equal(query.compute().to_pandas(), actual)


@pytest.mark.parametrize(
    "column_type, excluded_value, conversion",
    (("NUMERIC(18,2)", "'NaN'", "NaN"), ("NUMERIC", "1e51", "Rescaling")),
    ids=("constrained-nan", "unbounded-magnitude"),
)
def test_postgres_numeric_preserves_excluded_group_conversion_failure(
    column_type: str,
    excluded_value: str,
    conversion: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    semantic_project_factory: Callable[..., SemanticProject],
) -> None:
    data = SourceData(
        f"id BIGINT NOT NULL, bucket BIGINT NOT NULL, amount {column_type}",
        f"(1,7,11),(2,9,{excluded_value})",
        "",
        [],
    )
    monkeypatch.chdir(tmp_path)
    with source_case("postgres", "table", tmp_path, monkeypatch, data) as case:
        assert isinstance(case.source, TableSourceIR)
        semantic_project_factory(
            {
                "datasources/warehouse.py": _datasource_model("postgres", case, monkeypatch),
                "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Tests', default=True)",
                "sales/model.py": f"""import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table({case.source.table!r}, database={case.source.database!r}), primary_key=['id'])
bucket = ms.dimension_column(name='bucket', entity=orders, column='bucket')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all())
""",
            }
        )
        session = mv.session.get_or_create("numeric-conversion", timezone="UTC")
        bucket = catalog_value(session.catalog, "sales.orders.bucket", kind="dimension")
        amount = catalog_value(session.catalog, "sales.orders.amount", kind="measure")
        observed = session.observe({"value": amount.min()}, by=(bucket,))
        query = observed.fix(observed.axes[0], 7)

        with pytest.raises(MaterializationError) as baseline:
            compute_with_policy(query, _OFF)
        with pytest.raises(MaterializationError) as actual:
            query.compute()

        assert actual.value.stage == baseline.value.stage == "source_conversion"
        assert actual.value.received == baseline.value.received
        assert conversion in (actual.value.received or "")
        assert any(item.status == "refused" for item in _decisions(query))
