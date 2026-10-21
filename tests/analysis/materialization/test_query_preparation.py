"""R2.4 physical preparation over declared fields, time and versioned paths."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest
from ibis.common.deferred import Deferred

import marivo.analysis as mv
import marivo.datasource as md
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
finalized = ms.finalize()
amount_value = finalized.resolve(amount)
region_value = finalized.resolve(region)
buyer_value = finalized.resolve(buyer)
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
    return mv.session.create("preparation", timezone="Asia/Shanghai")


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
    fields = session.catalog.fields
    amount = fields.get("sales.orders.amount")
    region = fields.get("sales.customers.region")
    doubled = fields.get("sales.orders.doubled")
    assert (
        isinstance(amount, Deferred)
        and isinstance(region, Deferred)
        and isinstance(doubled, Deferred)
    )
    buyer = session.catalog.relationships.get("sales.buyer")
    seller = session.catalog.relationships.get("sales.seller")
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
    amount = session.catalog.fields.get("sales.orders.amount")
    time = session.catalog.fields.get(f"sales.orders.{field}")
    assert isinstance(amount, Deferred) and isinstance(time, Deferred)
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
    metric = prepared_session.catalog.metrics.get("sales.anchored")
    assert isinstance(metric, Deferred)
    with pytest.raises(SemanticExpressionError, match="path/attribute anchor"):
        prepared_session.catalog.preview(
            metric, scope=md.unpruned(max_rows=100, timeout_seconds=10)
        )
    with pytest.raises(SemanticExpressionError, match="path/attribute anchor"):
        prepared_session.catalog.preview_many(
            [prepared_session.catalog.metrics.get("sales.total"), metric],
            scope=md.unpruned(max_rows=100, timeout_seconds=10),
        )


def test_native_and_anchored_occurrences_are_distinct(prepared_session: Session) -> None:
    from marivo.semantic.expressions import Entity

    session = prepared_session
    region = session.catalog.fields.get("sales.customers.region")
    customers = session.catalog.entities.get("sales.customers")
    assert isinstance(region, Deferred) and isinstance(customers, Entity)
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
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.customers.region")
    assert isinstance(amount, Deferred) and isinstance(region, Deferred)
    buyer = session.catalog.relationships.get("sales.buyer")
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
    session = mv.session.create("duration", timezone="UTC")
    elapsed = session.catalog.fields.get("sales.facts.elapsed")
    assert isinstance(elapsed, Deferred)
    metrics = {
        "sum": elapsed.sum(),
        "mean": elapsed.mean(),
        "min": elapsed.min(),
        "max": elapsed.max(),
        "median": elapsed.median(),
        "quantile": elapsed.quantile(0.25),
        "twice": elapsed.sum() + elapsed.sum(),
        "half": elapsed.sum() / 2,
    }
    result = compute(session, metrics)
    expected = [2 * big + 2, big + 1, big, big + 2, big + 1, big + 1, 4 * big + 4, big + 1]
    for index, ticks in enumerate(expected):
        column = result.table[f"value.{index}"]
        assert str(column.dtype) == "duration[ns][pyarrow]"
        assert pa.array(column).cast(pa.int64()).to_pylist() == [ticks]
    with pytest.raises(SemanticExpressionError, match="non-NA"):
        compute(session, {"strict": elapsed.mean(nulls="error")})


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
    session = mv.session.create("cross-source", timezone="UTC")
    one = session.catalog.fields.get("sales.one.amount")
    two = session.catalog.fields.get("sales.two.amount")
    assert isinstance(one, Deferred) and isinstance(two, Deferred)
    result = compute(session, {"net": one.sum() - two.sum()})
    assert result.table["value.0"].tolist() == [7]


def test_civil_date_rejects_partial_business_day_boundary(prepared_session: Session) -> None:
    session = prepared_session
    amount = session.catalog.fields.get("sales.orders.amount")
    day = session.catalog.fields.get("sales.orders.day")
    assert isinstance(amount, Deferred) and isinstance(day, Deferred)
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
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.customers.region")
    assert isinstance(amount, Deferred) and isinstance(region, Deferred)
    buyer = session.catalog.relationships.get("sales.buyer")
    group = region.via((buyer,)).at(session.instant("2025-01-01T00:00:00+00:00"))
    result = compute(session, {"sum": amount.sum()}, by=(group,))
    assert sorted(result.table["value.0"].tolist()) == [10, 20]
    missing = region.via((buyer,)).at(session.instant("2025-01-02T00:00:00+00:00"))
    with pytest.raises(MaterializationError, match="exactly one matching"):
        compute(session, {"sum": amount.sum()}, by=(missing,))


@pytest.mark.parametrize("prepared_session", ["parsed_validity"], indirect=True)
def test_parsed_version_boundaries(prepared_session: Session) -> None:
    metric = prepared_session.catalog.metrics.get("sales.anchored")
    assert isinstance(metric, Deferred)
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
    metric = prepared_session.catalog.metrics.get("sales.anchored")
    assert isinstance(metric, Deferred)
    with pytest.raises(MaterializationError, match="loses source precision"):
        compute(prepared_session, {"sum": metric})
