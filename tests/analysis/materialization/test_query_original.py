"""Explicit source Metrics keep numerical and business identity contracts."""

from collections.abc import Callable
from pathlib import Path

import duckdb
import pytest

import marivo.analysis as mv
import marivo.semantic as ms
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject


@pytest.fixture
def original_source_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "original-source.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT,region VARCHAR,customer VARCHAR,"
            "channel VARCHAR,amount BIGINT,weight BIGINT,day DATE,profile_id BIGINT)"
        )
        connection.execute(
            "INSERT INTO orders VALUES "
            "(1,'East','A','Web',10,1,'2026-01-01',1),"
            "(2,'East','A','Store',30,3,'2026-01-02',1),"
            "(3,'West','B','Web',300,6,'2026-01-01',2)"
        )
        connection.execute("CREATE TABLE profiles(id BIGINT,segment VARCHAR)")
        connection.execute("INSERT INTO profiles VALUES (1,'Good'),(2,'Bad'),(2,'Duplicate')")
        connection.execute("CREATE TABLE snapshots(id BIGINT,day DATE,amount BIGINT)")
        connection.execute(
            "INSERT INTO snapshots VALUES (1,'2026-01-01',10),"
            "(1,'2026-01-02',30),(2,'2026-01-01',300)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\n"
            + "ms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
warehouse=ms.ref.datasource('warehouse')
orders=ms.entity(name='orders',datasource=warehouse,source=md.table('orders'),primary_key=['id'])
identity=ms.dimension_column(name='id',entity=orders,column='id')
region=ms.dimension_column(name='region',entity=orders,column='region')
customer=ms.dimension_column(name='customer',entity=orders,column='customer')
channel=ms.dimension_column(name='channel',entity=orders,column='channel')
amount=ms.measure_column(name='amount',entity=orders,column='amount',unit='CNY',additivity=ms.additive_all())
weight=ms.measure_column(name='weight',entity=orders,column='weight',additivity=ms.additive_all())
day=ms.time_dimension_column(name='day',entity=orders,column='day',granularity='day',is_default=True)
profile_id=ms.dimension_column(name='profile_id',entity=orders,column='profile_id')
profiles=ms.entity(name='profiles',datasource=warehouse,source=md.table('profiles'),primary_key=['id'])
profile_key=ms.dimension_column(name='id',entity=profiles,column='id')
segment=ms.dimension_column(name='segment',entity=profiles,column='segment')
profile=ms.relationship(name='profile',from_entity=orders,to_entity=profiles,keys=[ms.join_on(profile_id,profile_key)])
snapshots=ms.entity(name='snapshots',datasource=warehouse,source=md.table('snapshots'),primary_key=['id'],versioning=ms.snapshot(partition_field=ms.ref.time_dimension('sales.snapshots.day'),grain='day',timezone='UTC'))
snapshot_day=ms.time_dimension_column(name='day',entity=snapshots,column='day',granularity='day')
snapshot_amount=ms.measure_column(name='amount',entity=snapshots,column='amount',additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.create("original-source", timezone="UTC")


def test_raw_mean_and_weighted_pairs_finish_after_regrouping(
    original_source_session: Session,
) -> None:
    session = original_source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    weight = session.catalog.fields.get("sales.orders.weight")
    region = session.catalog.fields.get("sales.orders.region")
    fine = session.observe({"mean": amount.mean()}, by=(region,)).compute()
    assert fine.to_pandas()[("value", "mean")].tolist() == [20.0, 300.0]
    overall = session.observe(
        {"mean": amount.mean(), "weighted": amount.weighted_mean(weight=weight)}
    ).compute()
    assert overall.to_pandas()[("value", "mean")].tolist() == pytest.approx([340 / 3])
    assert overall.to_pandas()[("value", "weighted")].tolist() == [190.0]
    with duckdb.connect(str(session.project_root / "original-source.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id=3")
        connection.execute("UPDATE orders SET amount=NULL,weight=9 WHERE id=2")
    paired = session.observe({"weighted": amount.weighted_mean(weight=weight)}).compute()
    assert paired.to_pandas()[("value", "weighted")].tolist() == [10.0]


def test_original_constant_and_ratio_recompute_the_formula(
    original_source_session: Session,
) -> None:
    session = original_source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    original_ratio = session.observe({"ratio": amount.sum() / amount.count()}).compute()
    assert original_ratio.to_pandas()[("value", "ratio")].tolist() == pytest.approx([340 / 3])
    with duckdb.connect(str(session.project_root / "original-source.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id=2")
        connection.execute("UPDATE orders SET amount=100 WHERE id=1")
    fine = session.observe({"shifted": amount.sum() + 10}, by=(region,)).compute()
    assert fine.to_pandas()[("value", "shifted")].tolist() == [110, 310]
    assert fine.to_pandas()[("value", "shifted")].sum() == 420
    result = session.observe(
        {"shifted": amount.sum() + 10, "ratio": amount.sum() / amount.count()}
    ).compute()
    assert result.to_pandas()[("value", "shifted")].tolist() == [410]
    assert result.to_pandas()[("value", "ratio")].tolist() == [200.0]


@pytest.mark.parametrize(("customer", "expected"), [("A", 400), ("B", 200)])
def test_per_keeps_full_internal_identity(
    original_source_session: Session, customer: str, expected: int
) -> None:
    session = original_source_session
    with duckdb.connect(str(session.project_root / "original-source.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id=2")
        connection.execute("UPDATE orders SET amount=100 WHERE id=1")
        connection.execute("UPDATE orders SET customer=? WHERE id=3", [customer])
    amount = session.catalog.fields.get("sales.orders.amount")
    sample = session.catalog.fields.get("sales.orders.customer")
    region = session.catalog.fields.get("sales.orders.region")
    fine = session.observe({"value": amount.sum().per(sample).mean()}, by=(region,)).compute()
    assert fine.to_pandas()[("value", "value")].tolist() == [100.0, 300.0]
    result = session.observe({"value": amount.sum().per(sample).mean()}).compute()
    assert result.to_pandas()[("value", "value")].tolist() == [expected]


def test_exact_quantile_and_distinct_use_complete_values(original_source_session: Session) -> None:
    session = original_source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    customer = session.catalog.fields.get("sales.orders.customer")
    result = session.observe(
        {"quantile": amount.quantile(0.95), "distinct": customer.count_distinct()}
    ).compute()
    assert result.to_pandas()[("value", "quantile")].tolist() == pytest.approx([273.0])
    assert result.to_pandas()[("value", "distinct")].tolist() == [2]


def test_explicit_source_signed_sum_and_zero_denominator(original_source_session: Session) -> None:
    session = original_source_session
    with duckdb.connect(str(session.project_root / "original-source.duckdb")) as connection:
        connection.execute("DELETE FROM orders WHERE id=2")
        connection.execute("UPDATE orders SET amount=CASE id WHEN 1 THEN 100 ELSE -100 END")
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    fine = session.observe({"value": amount.sum()}, by=(region,)).compute()
    assert fine.to_pandas()[("value", "value")].tolist() == [100, -100]
    result = session.observe(
        {"sum": amount.sum(), "ratio": amount.count() / amount.sum()}
    ).compute()
    assert result.to_pandas()[("value", "sum")].tolist() == [0]
    assert result.to_pandas()[("value", "ratio")].isna().all()
    with pytest.raises(AnalysisError, match="denominator"):
        session.observe(
            {"ratio": amount.count().divide(amount.sum(), zero_denominator="error")}
        ).compute()


def test_member_count_uses_complete_identities_and_selected_fine_keys(
    original_source_session: Session,
) -> None:
    session = original_source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    members = session.members(session.catalog.require(ms.ref.entity("sales.orders")))
    fine = session.observe({"members": members.count()}, by=(region,))
    fixed = fine.where(fine.coord(fine.axes[0]) == "East").compute()
    keys = session.population_from(fixed, on=((fixed.axes[0], region),))
    with pytest.raises(AnalysisError, match="condition"):
        session.observe({"members": members.count()}, where=mv.in_population(keys))
    control = session.observe({"members": members.count()}).compute()
    assert control.to_pandas()[("value", "members")].tolist() == [3]
    selected_members = session.members(
        session.catalog.require(ms.ref.entity("sales.orders")), where=mv.in_population(keys)
    )
    result = session.observe({"members": selected_members.count()}).compute()
    assert result.to_pandas()[("value", "members")].tolist() == [2]
    overall = session.observe(
        {"members": members.count(), "ratio": amount.sum() / members.count()},
        where=mv.in_population(members),
    ).compute()
    assert overall.to_pandas()[("value", "members")].tolist() == [3]
    assert overall.to_pandas()[("value", "ratio")].tolist() == pytest.approx([340 / 3])


def test_member_count_drill_reuses_selected_complete_member_identities(
    original_source_session: Session,
) -> None:
    session = original_source_session
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    members = session.members(
        session.catalog.require(ms.ref.entity("sales.orders")), where=region == "East"
    )
    result = session.observe({"members": members.count()}, by=(region, channel)).compute()
    assert result.to_pandas()[("value", "members")].tolist() == [1, 1]
    assert len(result.to_pandas()) == 2


def test_version_coordinate_is_part_of_the_complete_contribution_identity(
    original_source_session: Session,
) -> None:
    session = original_source_session
    amount = session.catalog.fields.get("sales.snapshots.amount")
    first = session.instant("2026-01-01T12:00:00+00:00")
    second = session.instant("2026-01-02T12:00:00+00:00")
    first_members = session.members(
        session.catalog.require(ms.ref.entity("sales.snapshots")), at=first
    )
    second_members = session.members(
        session.catalog.require(ms.ref.entity("sales.snapshots")), at=second
    )
    one = session.read(first_members, {"amount": amount.at(first)}).compute().to_pandas()
    two = session.read(second_members, {"amount": amount.at(second)}).compute().to_pandas()
    assert one[("value", "amount")].tolist() == [10, 300]
    assert two[("value", "amount")].tolist() == [30]
    assert one[("value", "amount")].sum() + two[("value", "amount")].sum() == 340


def test_window_space_rollup_preserves_both_original_endpoints(
    original_source_session: Session,
) -> None:
    session = original_source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    day = session.catalog.fields.get("sales.orders.day")
    calendar = session.time_scope(start="2026-01-01", end="2026-01-03").by(mv.grain("day"))
    metric = amount.sum(time=day).window(mv.all_history())
    result = session.observe({"window": metric}, by=(calendar,)).compute().to_pandas()
    assert result[("value", "window")].tolist() == [310, 340]


def test_drill_reads_only_selected_contributions_and_recomputes_mean(
    original_source_session: Session,
) -> None:
    session = original_source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    fine = session.observe({"mean": amount.mean()}, by=(region,))
    fixed = fine.where(fine.coord(fine.axes[0]) == "East").compute()
    keys = session.population_from(fixed, on=((fixed.axes[0], region),))
    detail = session.observe(
        {"mean": amount.mean()}, by=(region, channel), where=mv.in_population(keys)
    ).compute()
    rows = detail.to_pandas()
    assert list(zip(rows.iloc[:, 0], rows.iloc[:, 1], rows[("value", "mean")], strict=True)) == [
        ("East", "Store", 30.0),
        ("East", "Web", 10.0),
    ]
    overall = session.observe({"mean": amount.mean()}, where=mv.in_population(keys)).compute()
    assert overall.to_pandas()[("value", "mean")].tolist() == [20.0]


def test_rollup_then_drill_keeps_per_identity_after_key_renumbering(
    original_source_session: Session,
) -> None:
    session = original_source_session
    amount = session.catalog.fields.get("sales.orders.amount")
    customer = session.catalog.fields.get("sales.orders.customer")
    channel = session.catalog.fields.get("sales.orders.channel")
    metric = amount.sum().per(customer).mean()
    overall = session.observe({"value": metric}).compute()
    assert overall.to_pandas()[("value", "value")].tolist() == [170]
    result = session.observe({"value": metric}, by=(channel,)).compute().to_pandas()
    assert result.iloc[:, 0].tolist() == ["Store", "Web"]
    assert result[("value", "value")].tolist() == [30, 155]


def test_drill_attribute_template_keeps_versions_and_selected_fiber_checks(
    original_source_session: Session,
) -> None:
    session = original_source_session
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    segment = session.catalog.fields.get("sales.profiles.segment")
    east = session.members(
        session.catalog.require(ms.ref.entity("sales.orders")), where=region == "East"
    )
    rows = session.read(east, {"channel": channel, "segment": segment}).compute().to_pandas()
    assert rows[("value", "channel")].tolist() == ["Web", "Store"]
    assert rows[("value", "segment")].tolist() == ["Good", "Good"]
    west = session.members(
        session.catalog.require(ms.ref.entity("sales.orders")), where=region == "West"
    )
    with pytest.raises(AnalysisError, match="exactly one matching scalar field row"):
        session.read(west, {"segment": segment}).compute()


def test_current_row_mean_differs_from_explicit_source_mean(
    original_source_session: Session,
) -> None:
    session = original_source_session
    with duckdb.connect(str(session.project_root / "original-source.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders SELECT i,CASE WHEN i=0 THEN 'A' ELSE 'B' END,'customer','Web',CASE WHEN i=0 THEN 100 ELSE 10 END,1,'2026-01-01',1 FROM range(91) t(i)"
        )
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    fixed = session.observe({"mean": amount.mean()}, by=(region,)).compute()
    assert fixed.to_pandas()[("value", "mean")].tolist() == [100.0, 10.0]
    rows = fixed.reduce({"mean": fixed["mean"].mean()}).compute()
    assert rows.to_pandas()[("value", "mean")].tolist() == [55.0]
    overall = session.observe({"mean": amount.mean()}).compute()
    assert overall.to_pandas()[("value", "mean")].tolist() == [1000 / 91]
    saved = fixed.persist()
    restored = session.persisted_frame(saved.artifact.id)
    assert restored.reduce({"mean": restored["mean"].mean()}).compute().to_pandas()[
        ("value", "mean")
    ].tolist() == [55.0]


def test_current_row_distinct_sum_differs_from_explicit_source_distinct(
    original_source_session: Session,
) -> None:
    session = original_source_session
    with duckdb.connect(str(session.project_root / "original-source.duckdb")) as connection:
        connection.execute("DELETE FROM orders")
        connection.execute(
            "INSERT INTO orders VALUES (1,'A','1','Web',0,1,'2026-01-01',1),(2,'A','2','Web',0,1,'2026-01-01',1),(3,'B','2','Web',0,1,'2026-01-01',1),(4,'B','3','Web',0,1,'2026-01-01',1)"
        )
    customer = session.catalog.fields.get("sales.orders.customer")
    region = session.catalog.fields.get("sales.orders.region")
    fixed = session.observe({"distinct": customer.count_distinct()}, by=(region,)).compute()
    assert fixed.to_pandas()[("value", "distinct")].tolist() == [2, 2]
    assert fixed.reduce({"count": fixed["distinct"].sum()}).compute().to_pandas()[
        ("value", "count")
    ].tolist() == [4]
    assert session.observe({"count": customer.count_distinct()}).compute().to_pandas()[
        ("value", "count")
    ].tolist() == [3]
