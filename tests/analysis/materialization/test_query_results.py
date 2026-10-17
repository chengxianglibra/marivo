"""Result expressions consume the same checked source/pandas stage input."""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import marivo.analysis as mv
from marivo.analysis.dsl.column import row_count
from marivo.analysis.errors import AnalysisError
from marivo.analysis.session.core import Session
from marivo.semantic.reader import SemanticProject


@pytest.fixture
def result_session(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Session:
    database = tmp_path / "results.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT, region VARCHAR, amount BIGINT, channel VARCHAR)"
        )
        connection.execute(
            "INSERT INTO orders VALUES (1,'A',10,'Web'),(2,'A',30,'Store'),(3,'B',20,'Web'),(4,'C',NULL,'Web')"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse', path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales', owner='Analytics', default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['id'])
region = ms.dimension_column(name='region', entity=orders, column='region')
channel = ms.dimension_column(name='channel', entity=orders, column='channel')
amount = ms.measure_column(name='amount', entity=orders, column='amount', additivity=ms.additive_all(), unit='CNY')
other_orders = ms.entity(name='other_orders', datasource=ms.ref.datasource('warehouse'), source=md.table('orders'), primary_key=['id'])
other_channel = ms.dimension_column(name='channel', entity=other_orders, column='channel')
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    return mv.session.get_or_create("results", report_timezone="UTC")


def test_select_where_top_and_independent_statistics(result_session: Session) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    q = session.observe({"sum": amount.sum(empty="zero"), "mean": amount.mean()}, by=(region,))
    assert q.select({"net": q["sum"] - 10}).execute().to_pandas()[("value", "net")].tolist() == [
        30,
        10,
        -10,
    ]
    selected = q.where(q["sum"] >= 20)
    result = selected.top(1, by=selected["sum"]).execute().to_pandas()
    assert result.iloc[:, 0].tolist() == ["A"]
    assert result[("value", "sum")].tolist() == [40]
    statistics = (
        q.reduce({"sum": q["sum"].sum(), "mean": q["mean"].mean(), "rows": row_count()})
        .execute()
        .to_pandas()
    )
    assert statistics[("value", "sum")].tolist() == [60]
    assert statistics[("value", "mean")].tolist() == [20.0]
    assert statistics[("value", "rows")].tolist() == [3]


def test_drill_reads_selected_fibers_and_recomputes_original_mean(result_session: Session) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    amount = session.catalog.fields.get("sales.orders.amount")
    fine = session.observe({"mean": amount.mean()}, by=(region,))
    eligible = fine.where(fine["mean"] == 20)
    selected = eligible.top(1, by=eligible["mean"])
    drilled = selected.drill(add=(channel,))
    frame = drilled.execute().to_pandas()
    assert frame.iloc[:, 0].tolist() == ["A", "A"]
    assert frame.iloc[:, 1].tolist() == ["Store", "Web"]
    assert frame[("value", "mean")].tolist() == [30.0, 10.0]
    assert drilled.rollup(by=(region,)).execute().to_pandas()[("value", "mean")].tolist() == [20.0]


def test_drill_member_count_keeps_complete_member_identity(result_session: Session) -> None:
    import marivo.semantic as ms

    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    fine = session.observe(
        {"people": mv.member_count()},
        by=(region,),
        members=session.members(ms.ref.entity("sales.orders")),
    )
    selected = fine.where(fine["people"] == 2)
    drilled = selected.drill(add=(channel,))
    assert drilled.execute().to_pandas()[("value", "people")].tolist() == [1, 1]
    assert drilled.rollup(by=(region,)).execute().to_pandas()[("value", "people")].tolist() == [2]


def test_public_drill_preserves_snapshot_row_identity(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "versions.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            "CREATE TABLE orders(id BIGINT,snapshot_day DATE,region VARCHAR,channel VARCHAR,amount BIGINT)"
        )
        connection.execute(
            "INSERT INTO orders VALUES(1,'2026-01-01','East','Web',10),(1,'2026-01-02','East','Store',30),(2,'2026-01-01','West','Other',300)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
orders=ms.entity(name='orders',datasource=ms.ref.datasource('warehouse'),source=md.table('orders'),primary_key=['id'],versioning=ms.snapshot(partition_field=ms.ref.time_dimension('sales.orders.snapshot_day'),grain='day',timezone='UTC'))
snapshot_day=ms.time_dimension_column(name='snapshot_day',entity=orders,column='snapshot_day',granularity='day')
region=ms.dimension_column(name='region',entity=orders,column='region')
channel=ms.dimension_column(name='channel',entity=orders,column='channel')
amount=ms.measure_column(name='amount',entity=orders,column='amount',additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("versions", report_timezone="UTC")
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    fine = session.observe({"total": amount.sum()}, by=(region,))
    selected = fine.where(fine["total"] < 100)
    drilled = selected.drill(add=(channel,))
    frame = drilled.execute().to_pandas()
    assert frame.iloc[:, 1].tolist() == ["Store", "Web"]
    assert frame[("value", "total")].tolist() == [30, 10]
    assert drilled.rollup().execute().to_pandas()[("value", "total")].tolist() == [40]


def test_result_division_policy_and_exact_domain_binding(result_session: Session) -> None:
    session = result_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    q = session.observe({"sum": amount.sum()}, by=(region,))
    divided = q.select({"ratio": q["sum"] / 0}).execute().to_pandas()
    assert divided[("value", "ratio")].isna().all()
    with pytest.raises(AnalysisError):
        q.select({"ratio": q["sum"].divide(0, zero_denominator="error")}).execute()
    other = session.observe({"sum": amount.sum()}, by=(region,))
    # Exact logical inputs can be reconstructed without Python object identity.
    assert q.select({"same": other["sum"]}).execute().to_pandas()[("value", "same")].to_dict() == {
        0: 40,
        1: 20,
        2: None,
    }
    with pytest.raises(AnalysisError):
        q.select({"bad": q.where(q["sum"] > 0)["sum"]})


def test_public_compare_retains_presence_and_projection_on_cold_read(
    result_session: Session,
) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    current = session.observe({"sales": amount.sum()}, by=(region,))
    baseline = session.values(
        by=(region,),
        columns={"sales": "CNY"},
        rows=((("A",), {"sales": -20}), (("B",), {"sales": 0}), (("D",), {"sales": 10})),
    )
    comparison = current.compare(baseline, how="outer")
    artifact = comparison.execute()
    frame = session.artifact(artifact.id).to_pandas()
    assert frame[("value", "sales.absolute")].iloc[:2].tolist() == [60, 20]
    assert frame[("value", "sales.relative")].iloc[0] == 3.0
    assert pd.isna(frame[("value", "sales.relative")].iloc[1])
    assert frame[("presence", ("current",))].tolist() == [True, True, True, False]
    assert frame[("presence", ("baseline",))].tolist() == [True, True, False, True]
    metadata = frame.attrs["marivo"]
    assert metadata["axes"][0]["projections"][0]["path"] == ("current",)
    selected = comparison.select({"delta": comparison["sales.absolute"]})
    assert selected.execute().to_pandas()[("presence", ("current",))].tolist() == [
        True,
        True,
        True,
        False,
    ]


def test_empty_reduction_keeps_scalar_and_checks(result_session: Session) -> None:
    session = result_session
    amount = session.catalog.fields.get("sales.orders.amount")
    q = session.observe(
        {"sum": amount.sum()}, by=(session.catalog.fields.get("sales.orders.region"),)
    )
    empty = q.top(0, by=q["sum"])
    result = (
        empty.reduce({"sum": empty["sum"].sum(), "mean": empty["sum"].mean(), "rows": row_count()})
        .execute()
        .to_pandas()
    )
    assert result[("value", "sum")].isna().all()
    assert result[("value", "mean")].isna().all()
    assert result[("value", "rows")].tolist() == [0]


def test_literal_values_and_saved_top_order(result_session: Session) -> None:
    from marivo.analysis.materialization.graph_reading import PreviewRead, TerminalReader

    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    q = session.values(
        by=(region,),
        columns={"budget": "CNY"},
        rows=((("A",), {"budget": 10}), (("B",), {"budget": 30}), (("C",), {"budget": 20})),
    )
    ranked = q.top(3, by=q["budget"])
    selected = ranked.where(ranked["budget"] > 10)
    artifact = selected.execute()
    assert artifact.to_pandas().iloc[:, 0].tolist() == ["B", "C"]
    preview = TerminalReader(session._runtime.store)._frame(artifact._manifest, PreviewRead(1))
    assert preview.iloc[:, 0].tolist() == ["B"]
    reopened = session.artifact(artifact.id)
    assert TerminalReader(session._runtime.store)._frame(reopened._manifest, PreviewRead(2)).iloc[
        :, 0
    ].tolist() == ["B", "C"]
    scalar = session.values(by=(), columns={"budget": "CNY"}, rows=(((), {"budget": 100}),))
    assert scalar.execute().to_pandas()[("value", "budget")].tolist() == [100]


def test_values_bind_real_key_types_and_preserve_exact_constants(
    result_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime
    from decimal import Decimal

    from marivo.datasource.adapters import SourceSession

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("literal Query must not read business values")

    monkeypatch.setattr(SourceSession, "execute", forbidden)
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    for field, key in ((region, 1), (amount, True)):
        query = session.values(
            by=(field,), columns={"budget": "CNY"}, rows=(((key,), {"budget": 1}),)
        )
        with pytest.raises(AnalysisError, match="coordinate"):
            query.execute()
    nullable = (
        session.values(by=(region,), columns={"budget": "CNY"}, rows=(((None,), {"budget": 1}),))
        .execute()
        .to_pandas()
    )
    assert pd.isna(nullable.iloc[0, 0])
    large = (
        session.values(by=(), columns={"budget": "CNY"}, rows=(((), {"budget": 2**70}),))
        .execute()
        .to_pandas()
    )
    assert large[("value", "budget")].tolist() == [Decimal(2**70)]
    mixed = (
        session.values(
            by=(region,),
            columns={"budget": "CNY"},
            rows=((("A",), {"budget": Decimal("1.5")}), (("B",), {"budget": 2.0})),
        )
        .execute()
        .to_pandas()
    )
    assert mixed[("value", "budget")].tolist() == [1.5, 2.0]
    for invalid in (float("nan"), datetime(2026, 1, 1)):
        with pytest.raises(AnalysisError):
            session.values(by=(), columns={"value": None}, rows=(((), {"value": invalid}),))


def test_query_population_uses_selected_keys_and_preserves_checks(result_session: Session) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    q = session.observe({"sum": amount.sum()}, by=(region,))
    chosen = q.where(q["sum"] > 20)
    fixed = mv.coordinates(by=(region,), keys=(("B",), (None,)))
    domain = chosen.population().union(fixed)
    result = session.query(domain).execute().to_pandas()
    assert pd.isna(result.iloc[0, 0])
    assert result.iloc[1:, 0].tolist() == ["A", "B"]
    strict = q.select({"bad": q["sum"].divide(0, zero_denominator="error")})
    with pytest.raises(AnalysisError):
        session.query(strict.population()).execute()


def test_explicit_members_and_member_count(result_session: Session) -> None:
    from marivo.analysis.dsl.metrics import member_count

    session = result_session
    orders = session.catalog.entities.get("sales.orders")
    amount = session.catalog.fields.get("sales.orders.amount")
    requested = mv.identities(orders, keys=((1,), (3,), (999,)))
    result = (
        session.observe(
            {
                "total": amount.sum(),
                "people": member_count(),
                "per_person": amount.sum() / member_count(),
            },
            members=requested,
        )
        .execute()
        .to_pandas()
    )
    assert result[("value", "total")].tolist() == [30]
    assert result[("value", "people")].tolist() == [3]
    assert result[("value", "per_person")].tolist() == [10.0]
    complete = (
        session.observe({"total": amount.sum()}, members=requested, by=(mv.member(),))
        .execute()
        .to_pandas()
    )
    assert complete.iloc[:, 0].tolist() == [1, 3, 999]
    assert complete[("value", "total")].to_dict() == {0: 10, 1: 20, 2: None}
    assert complete.attrs["marivo"]["axes"][0]["kind"] == "member"
    assert requested.classify(by=(mv.member(),)).axes == requested.axes
    assert session.query(requested.project(by=(mv.member(),))).execute().to_pandas().iloc[
        :, 0
    ].tolist() == [1, 3, 999]
    with pytest.raises(AnalysisError):
        session.observe({"total": amount.sum()}, by=(mv.member(),))
    other_channel = session.catalog.fields.get("sales.other_orders.channel")
    for identity in (mv.member(), requested.axes[0]):
        with pytest.raises(AnalysisError, match="direct classification fields"):
            session.observe(
                {"total": amount.sum()}, members=requested, by=(identity, other_channel)
            )
    with pytest.raises(AnalysisError):
        session.observe({"people": member_count()})
    empty = mv.identities(orders, keys=())
    result = (
        session.observe({"people": member_count(), "sum": amount.sum()}, members=empty)
        .execute()
        .to_pandas()
    )
    assert result[("value", "people")].tolist() == [0]
    assert result[("value", "sum")].isna().all()


def test_query_keys_drive_a_later_typed_source_read(result_session: Session) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    amount = session.catalog.fields.get("sales.orders.amount")
    q = session.observe({"sum": amount.sum()}, by=(region,))
    chosen = q.where(q["sum"] > 20).population()
    follow = session.observe({"selected": amount.sum().where(mv.in_population(chosen))})
    assert follow.execute().to_pandas()[("value", "selected")].tolist() == [40]


def test_dependent_source_uses_complete_null_safe_tuple_predicates(
    result_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marivo.datasource.adapters import CompiledRead, SourceSession

    session = result_session
    with duckdb.connect(str(session.project_root / "results.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=7,channel=NULL WHERE id=4")
    region = session.catalog.fields.get("sales.orders.region")
    channel = session.catalog.fields.get("sales.orders.channel")
    amount = session.catalog.fields.get("sales.orders.amount")
    chosen = session.values(
        by=(region, channel),
        columns={"flag": "1"},
        rows=(
            (("A", "Store"), {"flag": 1}),
            (("B", "Web"), {"flag": 1}),
            (("C", None), {"flag": 1}),
        ),
    ).population()
    statements: list[str] = []
    original = SourceSession.execute

    def record(owner: SourceSession, read: CompiledRead) -> pd.DataFrame:
        statements.append(read.sql)
        return original(owner, read)

    monkeypatch.setattr(SourceSession, "execute", record)
    result = (
        session.observe({"total": amount.sum()}, where=mv.in_population(chosen))
        .execute()
        .to_pandas()
    )
    assert result[("value", "total")].tolist() == [57]
    assert len(statements) == 1
    assert "UNION" not in statements[0]
    assert " OR " in " ".join(statements[0].split())
    assert "IS NOT DISTINCT FROM" in statements[0]
    import marivo.semantic as ms

    selected = session.members(ms.ref.entity("sales.orders"), where=mv.in_population(chosen))
    assert session.query(selected).execute().to_pandas().iloc[:, 0].tolist() == [2, 3, 4]
    remainder = session.members(ms.ref.entity("sales.orders"), where=~mv.in_population(chosen))
    assert session.query(remainder).execute().to_pandas().iloc[:, 0].tolist() == [1]


def test_session_read_preserves_identity_and_feeds_a_new_source(result_session: Session) -> None:
    session = result_session
    orders = session.catalog.entities.get("sales.orders")
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    people = mv.identities(orders, keys=((1,), (2,)))
    q = session.read(people, {"amount": amount, "region": region})
    result = q.execute().to_pandas()
    assert result.iloc[:, 0].tolist() == [1, 2]
    assert result[("value", "region")].tolist() == ["A", "A"]
    selected = q.where(q["amount"] > 10).population()
    assert session.observe({"total": amount.sum()}, members=selected).execute().to_pandas()[
        ("value", "total")
    ].tolist() == [30]
    missing = session.read(mv.identities(orders, keys=((999,),)), {"region": region})
    with pytest.raises(AnalysisError):
        missing.execute()


def test_public_original_rollup_and_share_use_the_same_selected_inputs(
    result_session: Session,
) -> None:
    session = result_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    fine = session.observe(
        {"sales": amount.sum(), "aov": amount.mean(), "shifted": amount.sum() + 10}, by=(region,)
    )
    chosen = fine.where(fine["sales"] > 0)
    coarse = chosen.rollup().execute().to_pandas()
    assert coarse[("value", "sales")].tolist() == [60]
    assert coarse[("value", "aov")].tolist() == [20.0]
    assert coarse[("value", "shifted")].tolist() == [70]
    shares = fine.share("sales").execute().to_pandas()
    assert shares[("value", "sales.share")].dropna().tolist() == pytest.approx([2 / 3, 1 / 3])
    assert shares[("value", "sales.share")].isna().tolist() == [False, False, True]
    pd.testing.assert_frame_equal(shares.iloc[:, :-1], fine.execute().to_pandas())
    first = fine.share("sales")
    assert first.where(first["sales"] > 20).execute().to_pandas()[
        ("value", "sales.share")
    ].tolist() == pytest.approx([2 / 3])
    with pytest.raises(AnalysisError):
        fine.share("shifted").execute()


def test_public_broadcast_and_source_population_predicate(result_session: Session) -> None:
    session = result_session
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    fine = session.observe({"sales": amount.sum(empty="zero")}, by=(region,))
    total = fine.rollup()
    reference = mv.broadcast(total["sales"], to=fine)
    ratio = fine.select({"ratio": fine["sales"] / reference}).execute().to_pandas()
    assert ratio[("value", "ratio")].tolist() == pytest.approx([2 / 3, 1 / 3, 0])
    selected = fine.where(fine["sales"] > 20).population()
    assert fine.where(mv.in_population(selected)).execute().to_pandas()[
        ("value", "sales")
    ].tolist() == [40]
    incomplete = session.values(
        by=(region,), columns={"budget": "CNY"}, rows=((("A",), {"budget": 10}),)
    )
    with pytest.raises(AnalysisError):
        fine.select(
            {"ratio": fine["sales"] / mv.broadcast(incomplete["budget"], to=fine)}
        ).execute()


def test_calendar_correspondence_population_projection_requires_presence(
    result_session: Session,
) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    group = mv.coordinates(by=(region,), keys=(("A",),))
    current = session.query(
        mv.product(
            session.time_scope(start="2026-10-01", end="2026-10-04").by(mv.grain("day")), group
        )
    )
    baseline = session.query(
        mv.product(
            session.time_scope(start="2026-09-01", end="2026-09-03").by(mv.grain("day")), group
        )
    )
    paired = current.align(baseline, match=mv.ordinal(), how="outer")
    axis = paired.population().axes[0]
    assert isinstance(axis, mv.CorrespondenceAxis)
    right = axis.projections[("right",)]
    with pytest.raises(AnalysisError):
        session.query(paired.population().project(by=(right,))).execute()
    selected = paired.where(paired["right_present"])
    output = session.query(selected.population().project(by=(right,))).execute()
    frame = session.artifact(output.id).to_pandas()
    assert len(frame) == 2
    assert frame.iloc[:, 0].dt.day.tolist() == [1, 2]


def test_two_datasource_composite_parameters_compare_and_terminal_read(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import marivo.semantic as ms
    from marivo.datasource.adapters import SourceSession

    databases = tuple(tmp_path / (name + ".duckdb") for name in ("crm", "billing"))
    for database, amounts in zip(databases, ((150, 50), (100, 25)), strict=True):
        with duckdb.connect(str(database)) as connection:
            connection.execute("CREATE TABLE accounts(tenant VARCHAR,id VARCHAR,amount BIGINT)")
            connection.executemany(
                "INSERT INTO accounts VALUES (?,?,?)",
                [("t1", "A", amounts[0]), ("t2", "A", amounts[1])],
            )
    semantic_project_factory(
        {
            **{
                f"datasources/{name}.py": "import marivo.datasource as md\n"
                + f"md.duckdb(name={name!r},path={str(database)!r})\n"
                for name, database in zip(("crm", "billing"), databases, strict=True)
            },
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
crm = ms.entity(name='crm',datasource=ms.ref.datasource('crm'),source=md.table('accounts'),primary_key=['tenant','id'])
billing = ms.entity(name='billing',datasource=ms.ref.datasource('billing'),source=md.table('accounts'),primary_key=['tenant','id'])
crm_tenant = ms.dimension_column(name='tenant',entity=crm,column='tenant')
crm_id = ms.dimension_column(name='id',entity=crm,column='id')
billing_tenant = ms.dimension_column(name='tenant',entity=billing,column='tenant')
billing_id = ms.dimension_column(name='id',entity=billing,column='id')
crm_amount = ms.measure_column(name='amount',entity=crm,column='amount',unit='CNY',additivity=ms.additive_all())
billing_amount = ms.measure_column(name='amount',entity=billing,column='amount',unit='CNY',additivity=ms.additive_all())
account_match = ms.relationship(name='account_match',from_entity=crm,to_entity=billing,keys=[ms.join_on(crm_tenant,billing_tenant),ms.join_on(crm_id,billing_id)])
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("two-sources", report_timezone="UTC")
    left_members = session.members(ms.ref.entity("sales.crm"))
    right_members = session.members(ms.ref.entity("sales.billing"))
    left_amount = session.catalog.fields.get("sales.crm.amount")
    right_amount = session.catalog.fields.get("sales.billing.amount")
    left = session.observe({"sales": left_amount.sum()}, by=left_members)
    right = session.observe({"sales": right_amount.sum()}, by=right_members)
    chosen = right.where(right["sales"] > 50).population()
    filtered = session.observe({"sales": left_amount.sum()}, where=mv.in_population(chosen))
    assert filtered.execute().to_pandas()[("value", "sales")].tolist() == [150]
    comparison = left.compare(
        right,
        match=mv.corresponding(via=(session.catalog.relationships.get("sales.account_match"),)),
    )
    artifact = comparison.execute()
    frame = artifact.to_pandas()
    assert frame[("value", "sales.absolute")].tolist() == [50, 25]
    assert frame[("presence", ("current",))].tolist() == [True, True]

    def unavailable(*args: object, **kwargs: object) -> None:
        raise AssertionError("terminal reads must not reopen sources")

    monkeypatch.setattr(SourceSession, "__enter__", unavailable)
    assert session.artifact(artifact.id).to_pandas().equals(frame)


def test_share_partitions_by_lawfully_coarsened_calendar(
    semantic_project_factory: Callable[..., SemanticProject],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "share-calendar.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE orders(id BIGINT,region VARCHAR,day DATE,amount BIGINT)")
        connection.execute(
            "INSERT INTO orders VALUES (1,'A','2026-01-30',10),(2,'A','2026-01-31',30),(3,'B','2026-01-30',20),(4,'A','2026-02-01',60),(5,'B','2026-02-01',40)"
        )
    semantic_project_factory(
        {
            "datasources/warehouse.py": f"import marivo.datasource as md\nmd.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": "import marivo.semantic as ms\nms.domain(name='sales',owner='Analytics',default=True)\n",
            "sales/model.py": """import marivo.datasource as md
import marivo.semantic as ms
orders = ms.entity(name='orders',datasource=ms.ref.datasource('warehouse'),source=md.table('orders'),primary_key=['id'])
region = ms.dimension_column(name='region',entity=orders,column='region')
day = ms.time_dimension_column(name='day',entity=orders,column='day',granularity='day')
amount = ms.measure_column(name='amount',entity=orders,column='amount',additivity=ms.additive_all())
""",
        },
        load=False,
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.get_or_create("calendar-share", report_timezone="UTC")
    amount = session.catalog.fields.get("sales.orders.amount")
    day = session.catalog.fields.get("sales.orders.day")
    region = session.catalog.fields.get("sales.orders.region")
    grid = session.time_scope(start="2026-01-30", end="2026-02-03").by(mv.grain("day"))
    monthly = grid.coarsen(mv.grain("month"))
    assert grid.coarsen(mv.grain("day")) is grid
    daily = session.observe(
        {"sales": amount.sum(time=day, empty="zero")}, during=grid, by=(region,)
    )
    coarse_statistics = (
        daily.reduce({"total": daily["sales"].sum(), "rows": mv.row_count()}, by=(monthly,))
        .execute()
        .to_pandas()
    )
    assert coarse_statistics[("value", "total")].tolist() == [60, 100]
    assert coarse_statistics[("value", "rows")].tolist() == [4, 4]
    assert daily.rollup(by=(monthly,)).execute().to_pandas()[("value", "sales")].tolist() == [
        60,
        100,
    ]
    shared = daily.share("sales", partition_by=(monthly,))
    frame = shared.execute().to_pandas()
    assert frame[("value", "sales")].tolist() == [10, 20, 30, 0, 60, 40, 0, 0]
    assert frame[("value", "sales.share")].tolist() == pytest.approx(
        [1 / 6, 1 / 3, 1 / 2, 0, 3 / 5, 2 / 5, 0, 0]
    )
    selected = shared.where(shared["sales"] >= 30)
    assert selected.execute().to_pandas()[("value", "sales.share")].tolist() == pytest.approx(
        [1 / 2, 3 / 5, 2 / 5]
    )
    weekly = session.time_scope(start="2026-01-30", end="2026-02-03").by(mv.grain("week"))
    weekly_query = session.observe({"sales": amount.sum(time=day, empty="zero")}, during=weekly)
    with pytest.raises(AnalysisError, match="one containing target bucket"):
        weekly_query.share("sales", partition_by=(weekly.coarsen(mv.grain("month")),))


@pytest.mark.parametrize("kind", ["integer", "float", "decimal", "duration"])
def test_column_sum_distinguishes_missing_samples_from_valid_zero(
    result_session: Session, kind: str
) -> None:
    session = result_session
    region = session.catalog.fields.get("sales.orders.region")
    raw = (1, 10, 10, 5)
    values = (
        tuple(timedelta(seconds=value) for value in raw)
        if kind == "duration"
        else tuple(Decimal(value) for value in raw)
        if kind == "decimal"
        else tuple(float(value) for value in raw)
        if kind == "float"
        else raw
    )
    base = session.values(
        by=(region,),
        columns={"value": "s" if kind == "duration" else None, "divisor": None, "group": None},
        rows=tuple(
            ((key,), {"value": value, "divisor": divisor, "group": group})
            for key, value, divisor, group in zip(
                "ABCD", values, (0, -1, 1, 1), (0, 1, 1, 2), strict=True
            )
        ),
    )
    for selected, expected, count in (
        (base.where(base["group"] < 0), None, 0),
        (base.where(base["group"] == 0), None, 1),
        (base, values[3], 4),
        (base.where(base["group"] == 1), values[0] * 0, 2),
        (base.where(base["group"] > 0), values[3], 3),
    ):
        query = selected.select(
            {"value": selected["value"] if count == 0 else selected["value"] / selected["divisor"]}
        )
        artifact = query.reduce({"sum": query["value"].sum(), "rows": row_count()}).execute()
        rows = artifact.to_pandas()
        actual = rows[("value", "sum")].iloc[0]
        assert pd.isna(actual) if expected is None else actual == expected
        assert rows[("value", "rows")].tolist() == [count]
        assert session.artifact(artifact.id).to_pandas().equals(rows)


def test_nullable_source_sum_preserves_large_integer_through_local_consumers(
    result_session: Session,
) -> None:
    session = result_session
    large = 2**53 + 1
    with duckdb.connect(str(session.project_root / "results.duckdb")) as connection:
        connection.execute("UPDATE orders SET amount=? WHERE id=1", [large])
        connection.execute("UPDATE orders SET amount=0 WHERE id=2")
    amount = session.catalog.fields.get("sales.orders.amount")
    region = session.catalog.fields.get("sales.orders.region")
    total = amount.sum()
    query = session.observe({"total": total, "shifted": total + 10}, by=(region,))
    artifact = query.execute()
    frame = artifact.to_pandas()
    assert frame[("value", "total")].to_dict() == {0: large, 1: 20, 2: None}
    assert frame[("value", "shifted")].to_dict() == {0: large + 10, 1: 30, 2: None}
    assert pd.api.types.is_integer_dtype(frame[("value", "total")].dtype)
    pd.testing.assert_frame_equal(session.artifact(artifact.id).to_pandas(), frame)
    pd.testing.assert_frame_equal(query.share("total").execute().to_pandas().iloc[:, :-1], frame)
    for reduced in (
        query.rollup(),
        query.reduce({"total": query["total"].sum()}),
        session.observe({"total": total.per(region).sum()}),
    ):
        assert reduced.execute().to_pandas()[("value", "total")].tolist() == [large + 20]
