"""R2.4 physical preparation over declared fields, time and versioned paths."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.datasource as md
import marivo.semantic as ms
from marivo._temporal import TimeScope
from marivo.analysis.compiler.graph_lowering import lower_query
from marivo.analysis.compiler.graph_plan import plan_query
from marivo.analysis.core.frame_data import (
    ExpressionFrameBacking,
    LiteralFrameBinding,
    SourceFrameBinding,
)
from marivo.analysis.core.graph import BoundGraph
from marivo.analysis.errors import AnalysisError
from marivo.analysis.materialization.errors import MaterializationError
from marivo.analysis.materialization.graph_exchange import StageOutput
from marivo.analysis.materialization.graph_source_execution import (
    execute_numeric_query,
    open_query_sources,
)
from marivo.analysis.session.core import Session
from marivo.semantic.errors import SemanticExpressionError
from marivo.semantic.reader import SemanticProject
from tests.shared_fixtures import catalog_value

MODEL = """import marivo.datasource as md
import marivo.semantic as ms
from marivo._temporal import InstantAnchor
warehouse = ms.ref.datasource("warehouse")
customers = ms.entity(name="customers", datasource=warehouse, source=md.table("customers"), primary_key=["id"], versioning=ms.validity(valid_from=ms.ref.time_dimension("sales.customers.since"), valid_to=ms.ref.time_dimension("sales.customers.until"), interval="closed_open", open_end=(None,)))
customer_id = ms.dimension_column(name="id", entity=customers, column="id")
since = ms.time_dimension_column(name="since", entity=customers, column="since")
until = ms.time_dimension_column(name="until", entity=customers, column="until")
region = ms.dimension_column(name="region", entity=customers, column="region")
orders = ms.entity(name="orders", datasource=warehouse, source=md.table("orders"), primary_key=["id"])
buyer_id = ms.dimension_column(name="buyer_id", entity=orders, column="buyer_id")
seller_id = ms.dimension_column(name="seller_id", entity=orders, column="seller_id")
amount = ms.measure_column(name="amount", entity=orders, column="amount", additivity=ms.additive_all())
@ms.dimension(entity=orders)
def doubled(rows):
    return rows.amount * 2
stamp = ms.time_dimension_column(name="stamp", entity=orders, column="stamp", parse=ms.strptime("%Y-%m-%d %H:%M:%S", timezone="Asia/Shanghai"), is_default=True)
day = ms.time_dimension_column(name="day", entity=orders, column="day")
naive = ms.time_dimension_column(name="naive", entity=orders, column="naive", parse=ms.timestamp(timezone="Asia/Shanghai"))
buyer = ms.relationship(name="buyer", from_entity=orders, to_entity=customers, keys=[ms.join_on(buyer_id, customer_id)])
seller = ms.relationship(name="seller", from_entity=orders, to_entity=customers, keys=[ms.join_on(seller_id, customer_id)])
amount_value = amount
region_value = region
buyer_value = buyer
anchor = InstantAnchor.fromisoformat("2026-01-01T00:00:00+00:00")
anchored_region = region_value.via((buyer_value,)).at(anchor)
@ms.metric()
def total():
    return amount_value.sum()
@ms.metric()
def anchored():
    return amount_value.sum().where(anchored_region == "old")
"""


@pytest.fixture
def prepared_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Session:
    parsed_versions = getattr(request, "param", None) == "parsed_validity"
    database = tmp_path / "warehouse.duckdb"
    connection = duckdb.connect(str(database))
    try:
        connection.execute(
            "CREATE TABLE customers(id BIGINT, region VARCHAR, since TIMESTAMPTZ, until TIMESTAMPTZ)".replace(
                "TIMESTAMPTZ", "VARCHAR"
            )
            if parsed_versions
            else "CREATE TABLE customers(id BIGINT, region VARCHAR, since TIMESTAMPTZ, until TIMESTAMPTZ)"
        )
        connection.execute(
            "INSERT INTO customers VALUES (1,'old','2025-01-01 00:00:00+00','2026-02-01 00:00:00+00'),(1,'new','2026-02-01 00:00:00+00',NULL),(2,'other','2025-01-01 00:00:00+00',NULL)"
        )
        if parsed_versions:
            connection.execute(
                "UPDATE customers SET since=split_part(since,'+',1), until=split_part(until,'+',1)"
            )
        connection.execute(
            "CREATE TABLE orders(id BIGINT,buyer_id BIGINT,seller_id BIGINT,amount BIGINT,stamp VARCHAR,day DATE,naive TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1,1,2,10,'2026-01-01 08:00:00','2026-01-01','2026-01-01 08:00:00'),(2,2,1,20,'2026-01-02 08:00:00','2026-01-02','2026-01-02 08:00:00')"
        )
    finally:
        connection.close()
    model = MODEL
    if parsed_versions:
        model = model.replace(
            'column="since")',
            'column="since", parse=ms.strptime("%Y-%m-%d %H:%M:%S", timezone="UTC"))',
        )
        model = model.replace(
            'column="until")',
            'column="until", parse=ms.strptime("%Y-%m-%d %H:%M:%S", timezone="UTC"))',
        )
    if getattr(request, "param", None) == "snapshot":
        model = model.replace(
            'ms.validity(valid_from=ms.ref.time_dimension("sales.customers.since"), valid_to=ms.ref.time_dimension("sales.customers.until"), interval="closed_open", open_end=(None,))',
            'ms.snapshot(partition_field=ms.ref.time_dimension("sales.customers.since"), timezone="UTC")',
        )
    if getattr(request, "param", None) == "precision":
        import pyarrow as pa
        import pyarrow.parquet as pq

        versions = tmp_path / "versions.parquet"
        pq.write_table(
            pa.table(
                {
                    "id": [1, 2],
                    "region": ["old", "other"],
                    "since": pa.array([1735689600000000001] * 2, type=pa.timestamp("ns", "UTC")),
                    "until": pa.array([None, None], type=pa.timestamp("ns", "UTC")),
                }
            ),
            versions,
        )
        model = model.replace(
            'source=md.table("customers")', f"source=md.parquet({str(versions)!r})"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f'import marivo.datasource as md\nmd.duckdb(name="warehouse",path={str(database)!r})\n',
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales",owner="Analytics",default=True)\n',
            "sales/models.py": model,
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("preparation", timezone="Asia/Shanghai")


def _source_graph(frame: mv.AnalysisFrame) -> BoundGraph:
    backing = frame._backing
    assert isinstance(backing, ExpressionFrameBacking)
    assert isinstance(backing.root, (SourceFrameBinding, LiteralFrameBinding))
    return backing.root.graph


def compute(
    session: Session,
    metrics: dict[str, Deferred],
    *,
    during: TimeScope | mv.CalendarAxis | None = None,
    by: tuple[Deferred, ...] = (),
    where: Deferred | None = None,
) -> StageOutput:
    graph = _source_graph(
        session.observe(metrics, by=by if during is None else (during, *by), where=where)
    )
    with open_query_sources(
        session._runtime.connection_service(), graph, session.catalog._state
    ) as prepared:
        return execute_numeric_query(
            prepared, lower_query(plan_query(graph, prepared.schemas), prepared.relations)
        )


def test_paths_anchors_and_computed_fields(prepared_session: Session) -> None:
    session = prepared_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    region = catalog_value(session.catalog, "sales.customers.region", kind="field")
    doubled = catalog_value(session.catalog, "sales.orders.doubled", kind="field")
    assert (
        isinstance(amount, (ms.RowExpression, ms.MetricExpression))
        and isinstance(region, (ms.RowExpression, ms.MetricExpression))
        and isinstance(doubled, (ms.RowExpression, ms.MetricExpression))
    )
    buyer = session.catalog.get("relationship:sales.buyer")
    seller = session.catalog.get("relationship:sales.seller")
    old = region.via((buyer,)).at(session.instant("2026-01-15"))
    new = region.via((buyer,)).at(session.instant("2026-02-15"))
    other = region.via((seller,)).at(session.instant("2026-01-15"))
    output = compute(session, {"sum": amount.sum()}, by=(old, new, other), where=doubled > 0)
    assert output.table.to_dict("list") == {
        "key.0": ["old", "other"],
        "key.1": ["new", "other"],
        "key.2": ["other", "old"],
        "value.0": [10, 20],
    }


@pytest.mark.parametrize("field", ("stamp", "day", "naive"))
def test_parsed_and_civil_time_periods(prepared_session: Session, field: str) -> None:
    session = prepared_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    time = catalog_value(session.catalog, f"sales.orders.{field}", kind="field")
    assert isinstance(amount, (ms.RowExpression, ms.MetricExpression)) and isinstance(
        time, (ms.RowExpression, ms.MetricExpression)
    )
    calendar = session.time_scope(start="2026-01-01", end="2026-01-03").by(mv.grain("day"))
    output = compute(session, {"sum": amount.sum(time=time)}, during=calendar)
    assert output.table["key.0"].tolist() == [0, 1]
    assert output.table["value.0"].tolist() == [10, 20]


def test_preview_rejects_anchor_before_opening(
    prepared_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import marivo.datasource.backends as backends

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("unqualified preview opened a datasource")

    monkeypatch.setattr(backends, "_build_backend_from_effective", forbidden)
    metric = prepared_session.catalog.get("metric:sales.anchored")
    assert isinstance(metric, (ms.RowExpression, ms.MetricExpression))
    with pytest.raises(SemanticExpressionError, match="path/attribute anchor"):
        prepared_session.catalog.preview(
            metric, scope=md.unpruned(max_rows=100, timeout_seconds=10)
        )
    with pytest.raises(SemanticExpressionError, match="path/attribute anchor"):
        prepared_session.catalog.preview_many(
            [prepared_session.catalog.get("metric:sales.total"), metric],
            scope=md.unpruned(max_rows=100, timeout_seconds=10),
        )


def test_native_and_anchored_occurrences_are_distinct(prepared_session: Session) -> None:
    from marivo.semantic.catalog import Entity

    session = prepared_session
    region = catalog_value(session.catalog, "sales.customers.region", kind="field")
    customers = session.catalog.get("entity:sales.customers")
    assert isinstance(region, (ms.RowExpression, ms.MetricExpression)) and isinstance(
        customers, Entity
    )
    predicate = (region == "new") & (region.at(session.instant("2026-01-15")) == "old")
    result = compute(session, {"count": customers.count()}, where=predicate)
    assert result.table["value.0"].tolist() == [1]


def test_window_excludes_unrelated_bad_mapping(prepared_session: Session) -> None:
    session = prepared_session
    connection = duckdb.connect(str(session.catalog.workspace_dir / "warehouse.duckdb"))
    try:
        connection.execute("UPDATE orders SET buyer_id=99 WHERE id=2")
    finally:
        connection.close()
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    region = catalog_value(session.catalog, "sales.customers.region", kind="field")
    assert isinstance(amount, (ms.RowExpression, ms.MetricExpression)) and isinstance(
        region, (ms.RowExpression, ms.MetricExpression)
    )
    buyer = session.catalog.get("relationship:sales.buyer")
    group = region.via((buyer,)).at(session.instant("2026-01-15"))
    result = compute(
        session,
        {"sum": amount.sum()},
        by=(group,),
        during=session.time_scope(start="2026-01-01", end="2026-01-02"),
    )
    assert result.table["value.0"].tolist() == [10]


def test_duration_reducers_keep_exact_ticks(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    big = 9007199254740993
    path = tmp_path / "durations.parquet"
    pq.write_table(
        pa.table(
            {"id": [1, 2, 3], "elapsed": pa.array([big, big + 2, None], type=pa.duration("ns"))}
        ),
        path,
    )
    semantic_project_factory(
        {
            "datasources/warehouse.py": 'import marivo.datasource as md\nmd.duckdb(name="warehouse",path=":memory:")\n',
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales",owner="Analytics",default=True)\n',
            "sales/models.py": f"""import marivo.datasource as md
import marivo.semantic as ms
warehouse=ms.ref.datasource("warehouse")
facts=ms.entity(name="facts",datasource=warehouse,source=md.parquet({str(path)!r}),primary_key=["id"])
elapsed=ms.measure_column(name="elapsed",entity=facts,column="elapsed",additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("duration", timezone="UTC")
    elapsed = catalog_value(session.catalog, "sales.facts.elapsed", kind="field")
    assert isinstance(elapsed, (ms.RowExpression, ms.MetricExpression))
    metrics = {
        "sum": elapsed.sum(),
        "mean": elapsed.mean(),
        "min": elapsed.min(),
        "max": elapsed.max(),
        "twice": elapsed.sum() + elapsed.sum(),
        "half": elapsed.sum() / 2,
    }
    result = compute(session, metrics)
    expected = [2 * big + 2, big + 1, big, big + 2, 4 * big + 4, big + 1]
    for index, ticks in enumerate(expected):
        column = result.table[f"value.{index}"]
        assert str(column.dtype) == "duration[ns][pyarrow]"
        assert pa.array(column).cast(pa.int64()).to_pylist() == [ticks]
    from marivo.analysis.errors import AnalysisStageUnavailableError
    from marivo.datasource.adapters import SourceSession

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Unsupported source Duration quantile read business data")

    with monkeypatch.context() as guarded:
        guarded.setattr(SourceSession, "execute", forbidden)
        for metric in (elapsed.median(), elapsed.quantile(0.25)):
            with pytest.raises(AnalysisStageUnavailableError, match=r"duration\[ns\]"):
                compute(session, {"value": metric})
    # Explicitly completed member samples retain their local exact tick kernel.
    members = session.members(session.catalog.get("entity:sales.facts"))
    samples = elapsed.sum().per(members)
    local = compute(session, {"median": samples.median(), "q": samples.quantile(0.25)})
    for name in ("value.0", "value.1"):
        assert pa.array(local.table[name]).cast(pa.int64()).to_pylist() == [big + 1]
    with pytest.raises(SemanticExpressionError, match="non-NA"):
        compute(session, {"strict": elapsed.mean(nulls="error")})


@pytest.mark.parametrize(
    "shape,bounded",
    [("direct", True), ("formula", True), ("direct", False), ("formula", False), ("source", False)],
)
def test_local_duration_sample_mean_uses_observed_samples_without_required_period(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bounded: bool,
    shape: str,
) -> None:
    from datetime import datetime, timezone

    import pyarrow as pa
    import pyarrow.parquet as pq

    from tests.support.source_trace import capture_source

    path = tmp_path / "duration_samples.parquet"
    pq.write_table(
        pa.table(
            {
                "id": [1, 2],
                "point": pa.array(
                    [datetime(2026, 10, 1, hour, tzinfo=timezone.utc) for hour in (0, 2)],
                    type=pa.timestamp("us", "UTC"),
                ),
                "elapsed": pa.array([10, 30], type=pa.duration("ns")),
            }
        ),
        path,
    )
    semantic_project_factory(
        {
            "datasources/warehouse.py": 'import marivo.datasource as md\nmd.duckdb(name="warehouse",path=":memory:")\n',
            "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales",owner="Analytics",default=True)\n',
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"facts=ms.entity(name='facts',datasource=ms.ref.datasource('warehouse'),source=md.parquet({str(path)!r}),primary_key=['id'])\n"
            + "point=ms.time_dimension_column(name='point',entity=facts,column='point',sample_interval=(1,'hour'))\n"
            + "elapsed=ms.measure_column(name='elapsed',entity=facts,column='elapsed',additivity=ms.additive_except(point),status_time_dimension=point)\n",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("duration-samples", timezone="UTC")
    elapsed = catalog_value(session.catalog, "sales.facts.elapsed", kind="field")
    point = catalog_value(session.catalog, "sales.facts.point", kind="field")
    child = elapsed.mean(time=point)
    metric = (
        child
        if shape == "source"
        else (child + child if shape == "formula" else child).per(point).mean()
    )
    scope = session.time_scope(start="2026-10-01", end="2026-10-01T03:00:00")
    trace = capture_source(monkeypatch)
    frame = session.observe({"v": metric}, by=(scope,) if bounded else ()).compute().to_pandas()
    assert pa.array(frame["v"]).cast(pa.int64()).to_pylist() == [40 if shape == "formula" else 20]
    assert trace.returned_rows
    assert all("__mean_folded" not in columns for columns in trace.returned_columns)


def test_independent_roots_across_datasources(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = {
        "sales/_domain.py": 'import marivo.semantic as ms\nms.domain(name="sales",owner="Analytics",default=True)\n'
    }
    model = "import marivo.datasource as md\nimport marivo.semantic as ms\n"
    for name, amount in (("one", 10), ("two", 3)):
        database = tmp_path / f"{name}.duckdb"
        connection = duckdb.connect(str(database))
        try:
            connection.execute("CREATE TABLE facts(id BIGINT, amount BIGINT)")
            connection.execute("INSERT INTO facts VALUES (1,?)", [amount])
        finally:
            connection.close()
        files[f"datasources/{name}.py"] = (
            f"import marivo.datasource as md\nmd.duckdb(name={name!r},path={str(database)!r})\n"
        )
        model += f'{name}=ms.entity(name={name!r},datasource=ms.ref.datasource({name!r}),source=md.table("facts"),primary_key=["id"])\n{name}_amount=ms.measure_column(name="amount",entity={name},column="amount",additivity=ms.additive_all())\n'
    files["sales/models.py"] = model
    semantic_project_factory(files, load=False)
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("cross-source", timezone="UTC")
    one = catalog_value(session.catalog, "sales.one.amount", kind="field")
    two = catalog_value(session.catalog, "sales.two.amount", kind="field")
    assert isinstance(one, (ms.RowExpression, ms.MetricExpression)) and isinstance(
        two, (ms.RowExpression, ms.MetricExpression)
    )
    result = compute(session, {"net": one.sum() - two.sum()})
    assert result.table["value.0"].tolist() == [7]


def test_civil_date_rejects_partial_business_day_boundary(prepared_session: Session) -> None:
    session = prepared_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    day = catalog_value(session.catalog, "sales.orders.day", kind="field")
    assert isinstance(amount, (ms.RowExpression, ms.MetricExpression)) and isinstance(
        day, (ms.RowExpression, ms.MetricExpression)
    )
    with pytest.raises(AnalysisError, match="business-midnight"):
        compute(
            session,
            {"sum": amount.sum(time=day)},
            during=session.time_scope(
                start="2026-01-01T02:00:00+08:00", end="2026-01-02T00:00:00+08:00"
            ),
        )


@pytest.mark.parametrize("prepared_session", ["snapshot"], indirect=True)
def test_snapshot_attribute_uses_exact_date(prepared_session: Session) -> None:
    session = prepared_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    region = catalog_value(session.catalog, "sales.customers.region", kind="field")
    assert isinstance(amount, (ms.RowExpression, ms.MetricExpression)) and isinstance(
        region, (ms.RowExpression, ms.MetricExpression)
    )
    buyer = session.catalog.get("relationship:sales.buyer")
    group = region.via((buyer,)).at(session.instant("2025-01-01T00:00:00+00:00"))
    result = compute(session, {"sum": amount.sum()}, by=(group,))
    assert sorted(result.table["value.0"].tolist()) == [10, 20]
    missing = region.via((buyer,)).at(session.instant("2025-01-02T00:00:00+00:00"))
    output = compute(session, {"sum": amount.sum()}, by=(missing,)).table
    assert output["key.0"].isna().all()
    assert output["value.0"].tolist() == [30]


@pytest.mark.parametrize("prepared_session", ["parsed_validity"], indirect=True)
def test_parsed_version_boundaries(prepared_session: Session) -> None:
    metric = prepared_session.catalog.get("metric:sales.anchored")
    assert isinstance(metric, (ms.RowExpression, ms.MetricExpression))
    result = compute(prepared_session, {"sum": metric})
    assert result.table["value.0"].tolist() == [10]


@pytest.mark.parametrize("prepared_session", ["precision"], indirect=True)
def test_version_axis_precision_is_checked_before_read(
    prepared_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import SourceSession

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("lossy version selection submitted a business read")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    metric = prepared_session.catalog.get("metric:sales.anchored")
    assert isinstance(metric, (ms.RowExpression, ms.MetricExpression))
    with pytest.raises(MaterializationError, match="loses source precision"):
        compute(prepared_session, {"sum": metric})


@pytest.mark.parametrize("bucket_count", (0, 1, 3, 129))
def test_calendar_bucket_keeps_nullable_int64_without_casting_case_null(
    prepared_session: Session, bucket_count: int
) -> None:
    from dataclasses import replace
    from datetime import date, timedelta, timezone

    import ibis
    import ibis.expr.datatypes as dt
    import sqlglot
    from sqlglot import expressions as sge

    from marivo._definition.types import CalendarCoordinate
    from marivo.analysis.materialization.query_preparation import prepare_fields
    from marivo.datasource.timezone import DatasourceEngineTimezone

    session = prepared_session
    start = date(2026, 1, 1)
    end = start + timedelta(days=bucket_count)
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    day = catalog_value(session.catalog, "sales.orders.day", kind="field")
    assert isinstance(amount, (ms.RowExpression, ms.MetricExpression)) and isinstance(
        day, (ms.RowExpression, ms.MetricExpression)
    )
    grid = session.time_scope(
        start=start.isoformat(), end=(start + timedelta(days=max(1, bucket_count))).isoformat()
    ).by(mv.grain("day"))
    graph = _source_graph(session.observe({"total": amount.sum(time=day)}, by=(grid,)))
    observation = graph.signature.observation
    assert observation is not None
    contribution = observation.contributions[0]
    key, calendar = contribution.calendars[0]
    assert isinstance(calendar, CalendarCoordinate)
    if not bucket_count:
        calendar = replace(calendar, cells=(), civil_bounds=())
    # A window input must classify rows outside the output grid as NULL instead
    # of filtering them away; exercise that preparation path without a read mask.
    contribution = replace(
        contribution, calendars=((key, calendar),), period=None, window_key=0, sample_axes=()
    )
    points = [
        start - timedelta(days=1),
        start,
        start + timedelta(days=1),
        end - timedelta(days=1),
        end,
        None,
    ]
    expected = (
        [None] * 6
        if not bucket_count
        else [None, 0, 1 if bucket_count > 1 else None, bucket_count - 1, None, None]
    )
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "bucket_events",
            ibis.memtable(
                {"id": list(range(6)), "amount": [1] * 6, "day": points},
                schema={"id": "int64", "amount": "int64", "day": "date"},
            ),
        )
        prepared = prepare_fields(
            contribution,
            {"sales.orders": table},
            session.catalog._state,
            DatasourceEngineTimezone("UTC", timezone.utc, "iana", "engine"),
        )
        buckets = [value for name, value in prepared.originals if name.startswith("__bucket_")]
        assert len(buckets) == 1
        bucket = buckets[0]
        assert bucket.type() == dt.Int64(nullable=True)
        projection = prepared.table.select("id", bucket=bucket).order_by("id")
        assert backend.to_pyarrow(projection).column("bucket").to_pylist() == expected
        for dialect in ("clickhouse", "duckdb", "postgres", "mysql", "trino"):
            ast = sqlglot.parse_one(ibis.to_sql(projection, dialect=dialect), read=dialect)
            cases = [
                alias.this
                for alias in ast.find_all(sge.Alias)
                if (alias.alias == "bucket" or alias.alias.startswith("__bucket_"))
                and isinstance(alias.this, sge.Case)
            ]
            if not bucket_count:
                assert not cases
                continue
            assert cases
            for case in cases:
                assert isinstance(case.args["default"], sge.Null)
                assert len(case.args["ifs"]) == bucket_count
                for branch in case.args["ifs"]:
                    result = branch.args["true"]
                    assert isinstance(result, sge.Cast)
                    assert result.to.this == sge.DataType.Type.BIGINT
                    assert not result.to.args.get("nullable")
    finally:
        backend.disconnect()


def _assert_no_boolean_identity_operands(sql: str, dialect: str) -> None:
    import sqlglot
    from sqlglot import expressions as sge

    ast = sqlglot.parse_one(sql, read=dialect)
    for operation in ast.find_all(sge.And, sge.Or):
        identity = isinstance(operation, sge.And)
        for operand in (operation.this, operation.expression):
            operand = operand.unnest()
            assert not (isinstance(operand, sge.Boolean) and operand.this is identity), (
                operation.sql(dialect=dialect)
            )


@pytest.mark.parametrize(
    ("selections", "expected_ids"),
    (
        pytest.param(((0,),), (1,), id="single"),
        pytest.param(((0, 1),), (1, 2), id="adjacent"),
        pytest.param(((0, 2),), (1, 3), id="disjoint"),
        pytest.param(((0, 1), (1, 2)), (2,), id="intersection"),
        pytest.param(((),), (), id="empty"),
        pytest.param(((0, 1), ()), (), id="empty-intersection"),
        pytest.param((), (0, 1, 2, 3, 4, 5), id="unconstrained"),
    ),
)
def test_calendar_selection_preserves_membership_without_boolean_seeds(
    prepared_session: Session,
    selections: tuple[tuple[int, ...], ...],
    expected_ids: tuple[int, ...],
) -> None:
    from dataclasses import replace
    from datetime import date, timedelta, timezone

    import ibis

    from marivo._definition.types import CalendarCoordinate
    from marivo.analysis.materialization.query_preparation import prepare_fields
    from marivo.datasource.timezone import DatasourceEngineTimezone

    session = prepared_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    day = catalog_value(session.catalog, "sales.orders.day", kind="field")
    assert isinstance(amount, (ms.RowExpression, ms.MetricExpression)) and isinstance(
        day, (ms.RowExpression, ms.MetricExpression)
    )
    grid = session.time_scope(start="2026-01-01", end="2026-01-04").by(mv.grain("day"))
    graph = _source_graph(session.observe({"total": amount.sum(time=day)}, by=(grid,)))
    observation = graph.signature.observation
    assert observation is not None
    contribution = observation.contributions[0]
    calendar = contribution.calendars[0][1]
    assert isinstance(calendar, CalendarCoordinate)
    contribution = replace(
        contribution,
        period=None,
        calendars=tuple(
            (
                f"key.{index}",
                replace(
                    calendar,
                    cells=tuple(calendar.cells[cell] for cell in selected),
                    civil_bounds=tuple(calendar.civil_bounds[cell] for cell in selected),
                ),
            )
            for index, selected in enumerate(selections)
        ),
    )
    start = date(2026, 1, 1)
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "selection_events",
            ibis.memtable(
                {
                    "id": list(range(6)),
                    "amount": [1] * 6,
                    "day": [start + timedelta(days=offset) for offset in (-1, 0, 1, 2, 3)] + [None],
                },
                schema={"id": "int64", "amount": "int64", "day": "date"},
            ),
        )
        prepared = prepare_fields(
            contribution,
            {"sales.orders": table},
            session.catalog._state,
            DatasourceEngineTimezone("UTC", timezone.utc, "iana", "engine"),
        )
        selected_name = dict(prepared.columns).get("__time_selected")
        if selections:
            assert selected_name is not None
            selected = prepared.table[selected_name]
            assert selected.type().is_boolean()
            projection = prepared.table.select("id", selected=selected).order_by("id")
            result = backend.to_pyarrow(projection)
            assert result.column("selected").to_pylist() == [True] * len(expected_ids)
        else:
            assert selected_name is None
            projection = prepared.table.select("id").order_by("id")
            result = backend.to_pyarrow(projection)
        assert result.column("id").to_pylist() == list(expected_ids)
        for dialect in ("clickhouse", "duckdb", "postgres", "mysql", "trino"):
            _assert_no_boolean_identity_operands(ibis.to_sql(projection, dialect=dialect), dialect)
    finally:
        backend.disconnect()


def test_open_start_native_period_keeps_only_upper_bound(prepared_session: Session) -> None:
    from dataclasses import replace
    from datetime import date, timedelta, timezone

    import ibis

    from marivo._definition.observations import ObservationPeriod
    from marivo.analysis.materialization.query_preparation import prepare_fields
    from marivo.datasource.timezone import DatasourceEngineTimezone

    session = prepared_session
    amount = catalog_value(session.catalog, "sales.orders.amount", kind="field")
    day = catalog_value(session.catalog, "sales.orders.day", kind="field")
    assert isinstance(amount, (ms.RowExpression, ms.MetricExpression)) and isinstance(
        day, (ms.RowExpression, ms.MetricExpression)
    )
    graph = _source_graph(session.observe({"total": amount.sum(time=day)}))
    observation = graph.signature.observation
    assert observation is not None
    contribution = replace(
        observation.contributions[0],
        period=ObservationPeriod(None, session.instant("2026-01-03"), "Asia/Shanghai"),
    )
    start = date(2026, 1, 1)
    backend = ibis.duckdb.connect()
    try:
        table = backend.create_table(
            "period_events",
            ibis.memtable(
                {
                    "id": list(range(5)),
                    "amount": [1] * 5,
                    "day": [start + timedelta(days=offset) for offset in (-2, 0, 1, 2)] + [None],
                },
                schema={"id": "int64", "amount": "int64", "day": "date"},
            ),
        )
        # A computed date has no raw lineage, so membership uses the native
        # fallback rather than the independently qualified raw range builder.
        table = table.mutate(day=table.day + ibis.interval(days=1))
        prepared = prepare_fields(
            contribution,
            {"sales.orders": table},
            session.catalog._state,
            DatasourceEngineTimezone("UTC", timezone.utc, "iana", "engine"),
        )
        period = prepared.table[dict(prepared.columns)["__time_period"]]
        assert period.type().is_boolean()
        projection = prepared.table.select("id", period=period).order_by("id")
        result = backend.to_pyarrow(projection)
        assert result.column("id").to_pylist() == [0, 1]
        assert result.column("period").to_pylist() == [True, True]
        for dialect in ("clickhouse", "duckdb", "postgres", "mysql", "trino"):
            _assert_no_boolean_identity_operands(ibis.to_sql(projection, dialect=dialect), dialect)
    finally:
        backend.disconnect()
